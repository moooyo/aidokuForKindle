#!/usr/bin/env python3
"""Run one frozen normal-card visit on test-env; publish only after verified return."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import secrets
import shlex
import signal
import sys
import tarfile
import zlib


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
RENDERED = HERE / 'rendered.py'
if not RENDERED.is_file():
    RENDERED = HERE.parent / 'startup-followup-20261121/rendered-library/host.py'
rendered = load_module('jpeg_native_rendered', RENDERED)
common = rendered.common
analyze = load_module('jpeg_native_analysis', HERE / 'analyze.py')
HEX = re.compile(r'[0-9a-f]{16}')
PREFIX = 'aidoku-jpeg-'
REPLIES = {'guard': b'guard_admitted\n', 'admit': b'jpeg_admitted\n', 'prepare': b'jpeg_prepared\n',
           'visit': b'jpeg_completed\n', 'close': b'jpeg_closed\n', 'absent': b'jpeg_absent\n'}
MEMBERS = {'origin', 'anchor', 'pointer-brackets', 'qualified', 'submission'}
NUMBER = rb'([1-9][0-9]{0,19})'
ZERO_NUMBER = rb'(0|[1-9][0-9]{0,19})'
BRACKETS = re.compile(b' '.join([NUMBER] * 4) + rb'\n')
SUBMIT = re.compile(rb'SUBMISSION ' + b' '.join([ZERO_NUMBER, ZERO_NUMBER, NUMBER, NUMBER])
                    + rb' (large|compact) (nonempty|empty) (nonempty|empty)\n')
common.REPLIES = REPLIES
common.SAFE_REFUSALS |= frozenset(('submission_prepare', 'submission_bind', 'submission_collect',
    'submission_evidence', 'programs_prepare', 'programs_select', 'programs_restore', 'programs_verify',
    'case_binding', 'selected_arm', 'arm_selection', 'program_guard', 'program_restore'))


def read_json(path):
    return common.unique_json(common.read_small(path, 256 * 1024))


def binding(cohort, owner, slot, label):
    common.need(isinstance(cohort, str) and HEX.fullmatch(cohort) and isinstance(label, str)
                and HEX.fullmatch(label) and cohort != label and type(slot) is int
                and 1 <= slot <= len(analyze.SCHEDULE), 'invalid_visit_identity')
    arm = analyze.SCHEDULE[slot - 1]
    suffix = 'warmup' if slot <= 2 else f'{(slot - 1) // 2:02d}'
    return {'schema': 1, 'cohort': cohort, 'slot': slot, 'arm': arm, 'owner': common.owner_id(owner),
            'case': ('control' if arm == 'A' else 'candidate') + '-' + suffix, 'label': label}


def checked_plan(work, owner, *, payload=True):
    plan = common.private_directory(work / 'plan')
    common.need(common.read_small(plan / 'ready', 16) == b'ready\n', 'plan_not_ready')
    record = read_json(plan / 'plan.json')
    common.need(record.get('schema') == 1 and record.get('owner') == common.owner_id(owner)
                and isinstance(record.get('cohort'), str) and HEX.fullmatch(record['cohort']), 'invalid_plan')
    sources = {'host.py': HERE / 'host.py', 'rendered.py': RENDERED,
               'common.py': Path(common.__file__), 'analyze.py': Path(analyze.__file__)}
    for name, source in sources.items():
        common.need(common.same_file(plan / name, source), 'operator_differs_from_frozen_plan')
    for name in ('device.sh', 'guard.sh', 'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
        common.read_small(plan / name)
    common.need(os.access(plan / 'common.py', os.X_OK), 'askpass_script_must_be_executable')
    if payload:
        for arm in 'AB':
            common.regular(plan / arm / 'payload.tar.gz', common.MAX_PAYLOAD)
    return record


class Transport(rendered.Transport):
    def __init__(self, argv, plan, visit, secret, *, recover=False):
        self.argv, self.label, self.secret = argv, visit['label'], secret
        self.case, self.recovery = visit['case'], recover
        common.need(re.fullmatch(r'(?:control|candidate)-(?:warmup|0[1-6])', self.case), 'invalid_case')
        prelude = f'if [ "$1" = prepare ] && [ "$3" = tapcheck ]; then set -- prepare "$2" {shlex.quote(self.case)}; fi\n'
        self.device = prelude.encode('ascii') + common.read_small(plan / 'device.sh', 128 * 1024)
        self.guard = common.read_small(plan / 'guard.sh', 128 * 1024)
        self.fds, self.last_stats = [], None
        try:
            self.fds.append(common.memfile('jpeg-device', self.device))
            if not recover:
                payload = common.regular(plan / visit['arm'] / 'payload.tar.gz', common.MAX_PAYLOAD)
                self.fds.append(common.memfile('jpeg-guard', self.guard))
                self.fds.append(common.memfile('jpeg-visit', self.device, payload.read_bytes()))
        except BaseException:
            self.close()
            raise

    def __call__(self, operation):
        kind, _, case = operation.partition(':')
        common.need(not self.recovery or operation in ('close', 'absent'), 'recovery_allows_close_only')
        common.need(kind not in ('visit', 'collect') or case == self.case, 'invalid_case')
        return super().__call__(operation)


def operate(lease, request, remote, visit, attempt, retained):
    rendered.CASES = (visit['case'],)
    rendered.REPLIES = REPLIES
    rendered.archive_members = lambda data, case: data
    def witnessed(operation):
        if operation == 'visit:' + visit['case']:
            common.checkpoint(attempt / 'card-intent.json', visit)
        try:
            return remote(operation)
        finally:
            witnessed.last_stats = getattr(remote, 'last_stats', None)
    return rendered.operate(lease=lease, request=request, remote=witnessed,
        save=lambda report: common.checkpoint(attempt / 'operations.json', report),
        save_evidence=lambda case, data: retained.append(data))


def extract_record(data, case):
    common.need(isinstance(data, bytes) and 10 <= len(data) <= rendered.MAX_ARCHIVE, 'invalid_archive')
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(data, rendered.MAX_TAR + 1)
        common.need(len(raw) <= rendered.MAX_TAR and len(raw) % 512 == 0 and decoder.eof
                    and not decoder.unused_data and not decoder.unconsumed_tail, 'invalid_archive')
        found, offset = {}, 0
        while offset < len(raw):
            block = raw[offset:offset + 512]
            if not any(block):
                common.need(len(raw) - offset >= 1024 and not any(raw[offset:]), 'invalid_archive')
                break
            item = tarfile.TarInfo.frombuf(block, 'utf-8', 'strict')
            prefix = f'out/{case}/'
            name = item.name.removeprefix(prefix)
            common.need(item.name.startswith(prefix) and name in MEMBERS and name not in found
                        and item.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not item.sparse
                        and 0 < item.size <= 256, 'unexpected_evidence_member')
            start = offset + 512
            end = start + item.size
            offset = start + ((item.size + 511) // 512) * 512
            common.need(offset <= len(raw) and not any(raw[end:offset]), 'invalid_archive')
            found[name] = raw[start:end]
        else:
            raise common.Stop('invalid_archive')
    except (zlib.error, tarfile.TarError, UnicodeError, ValueError):
        raise common.Stop('invalid_archive') from None
    common.need(set(found) == MEMBERS and found['origin'] == b'stock-tap\n'
                and found['qualified'] == f'{case} stock-tap\n'.encode('ascii')
                and found['anchor'] == found['pointer-brackets'], 'unbound_stock_visit')
    pointer, submitted = BRACKETS.fullmatch(found['anchor']), SUBMIT.fullmatch(found['submission'])
    common.need(pointer and submitted, 'invalid_submission')
    down_before, down_after, up_before, up_after = map(int, pointer.groups())
    lower, upper, sequence, marker = map(int, submitted.groups()[:4])
    layout, library, grid = [part.decode('ascii') for part in submitted.groups()[4:]]
    common.need(0 < down_before <= down_after < up_before <= up_after < 2**64
                and down_after - down_before <= 100_000 and up_after - up_before <= 100_000
                and up_before - down_after >= 50_000 and up_after + lower == up_before + upper < 2**64
                and 0 <= lower <= upper <= 150_000_000 and 0 < sequence < 2**64 and 0 < marker < 2**32
                and (layout, library, grid) == ('large', 'nonempty', 'nonempty'), 'unqualified_submission')
    return {'start': 'stock_card_release', 'end': 'library_frame_submission_callback', 'lower_us': lower,
            'upper_us': upper, 'layout': layout, 'library': library, 'grid': grid}


def archive_after_return(visit, attempt, report, retained):
    if not retained or report.get('released') is not True:
        return
    verify_return(visit, report)
    common.need(len(retained) == 1, 'ambiguous_evidence')
    try:
        extract_record(retained[0], visit['case'])
    except common.Stop:
        return
    common.write_new(attempt / 'case.tar.gz', retained[0])


def verify_return(visit, report):
    request = {'owner_thread': visit['owner'], 'run_id': PREFIX + visit['label']}
    identity, acquired, released = (report.get(key) for key in ('identity', 'acquisition', 'release'))
    common.need(report.get('schema') == 1 and report.get('request') == request and report.get('released') is True
                and report.get('recovery_required') is False and report.get('transport_unreaped') is not True
                and report.get('cases') == [visit['case']] and isinstance(identity, dict)
                and identity.get('project') == 'Aidoku' and identity.get('owner_thread') == visit['owner']
                and identity.get('run_id') == request['run_id'] and isinstance(acquired, dict)
                and isinstance(released, dict) and all(isinstance(identity.get(key), str) and identity[key]
                    and acquired.get(key) == released.get(key) == identity[key] for key in common.IDENTITY)
                and acquired.get('ok') is True and acquired.get('operation') == 'acquire' and acquired.get('state') == 'held'
                and released.get('operation') == 'release' and released.get('state') == 'released'
                and all(released.get(key) is True for key in ('ok', 'owner_and_token_checked',
                    'lease_directory_absent_after_release', 'native_restored_and_connections_closed')),
                'unverified_ownership_or_return')
    operations = report.get('operations')
    common.need(isinstance(operations, list) and all(isinstance(row, dict)
                and isinstance(row.get('operation'), str) for row in operations), 'invalid_operations')
    if report.get('mutation_possible') is True:
        close = [index for index, row in enumerate(operations) if row.get('operation') == 'close' and row.get('outcome') == 'ok']
        common.need(report.get('close_confirmed') is True and report.get('absence_confirmed') is True and close
                    and operations[-1].get('operation') == 'absent' and operations[-1].get('outcome') == 'ok'
                    and close[-1] < len(operations) - 1, 'unverified_device_closeout')
    else:
        common.need(report.get('mutation_possible') is False and all(row.get('operation') in ('guard', 'admit')
                    for row in operations), 'unverified_read_only_attempt')
    return operations


def reached_visit(attempt, visit, operations, report):
    intent = attempt / 'card-intent.json'
    if os.path.lexists(intent):
        common.need(read_json(intent) == visit, 'unbound_card_intent')
        return True
    common.need(not any(row.get('operation', '').partition(':')[0] in ('visit', 'collect') for row in operations)
                and not report.get('completed'), 'missing_card_intent')
    return False


def publish(work, visit, attempt, report):
    operations = verify_return(visit, report)
    if not reached_visit(attempt, visit, operations, report):
        return 'no_card_visit'
    case = visit['case']
    names = [row.get('operation') for row in operations]
    primary = ['guard', 'admit', 'prepare', 'visit:' + case, 'collect:' + case]
    entered = primary[:4] == names[:4] and all(row.get('outcome') == 'ok' for row in operations[:4])
    complete = (report.get('operations_passed') is True and report.get('completed') == [case] and entered
                and names == primary + ['close', 'absent'] and all(row.get('outcome') == 'ok' for row in operations))
    interval = None
    if complete:
        try:
            interval = extract_record(common.read_small(attempt / 'case.tar.gz', rendered.MAX_ARCHIVE), case)
        except (OSError, common.Stop):
            pass
    release = report['release']
    receipt = {'cohort': visit['cohort'], 'slot': visit['slot'], 'arm': visit['arm'], 'interval': interval,
               'result': 'measured' if interval else 'not_measured', 'closeout': {
                   'ordinary_exit': entered, 'native_restored': report.get('close_confirmed') is True,
                   'close': report.get('close_confirmed') is True, 'absent': report.get('absence_confirmed') is True,
                   'connections_closed': release['native_restored_and_connections_closed'],
                   'owner_and_token_checked': release['owner_and_token_checked'], 'released': report['released']}}
    path = work / 'receipts' / f'{visit["slot"]:02}.json'
    if os.path.lexists(path):
        common.need(read_json(path) == receipt, 'issued_slot_differs')
    else:
        analyze.issue(work / 'receipts', receipt)
    return receipt['result']


def untouched_collision(report):
    return (report.get('schema') == 1 and report.get('error') == 'lease_busy'
            and report.get('identity') is None and report.get('mutation_possible') is False
            and report.get('operations') == [] and report.get('recovery_required') is False)


def next_slot(work, cohort, owner, slot):
    receipts = common.private_directory(work / 'receipts')
    previous = analyze.read_receipts(receipts, cohort)
    common.need(slot == len(previous) + 1 and all(row['result'] == 'measured' for row in previous),
                'slot_not_next_or_failed_prefix')
    for prior in common.private_directory(work / 'visits').iterdir():
        common.private_directory(prior)
        match = re.fullmatch(r'([0-9]{2})-([0-9a-f]{16})', prior.name)
        common.need(match and 1 <= int(match[1]) <= slot, 'unexpected_visit_record')
        visit = binding(cohort, owner, int(match[1]), match[2])
        common.need(read_json(prior / 'attempt.json') == visit, 'unbound_attempt')
        report = read_json(prior / 'operations.json')
        if untouched_collision(report):
            continue
        operations = verify_return(visit, report)
        if visit['slot'] == slot:
            common.need(not reached_visit(prior, visit, operations, report), 'previous_visit_requires_publication')


def brief(report):
    allowed = ('operations_passed', 'released', 'recovery_required', 'close_confirmed', 'absence_confirmed')
    result = {key: report.get(key) for key in allowed}
    for key in ('error', 'close_error', 'release_error', 'recovery_error'):
        value = report.get(key)
        if isinstance(value, str) and re.fullmatch(r'[a-z0-9_:.-]{1,120}', value):
            result[key] = value
    tags = [row['refusal'] for row in report.get('operations', []) if row.get('refusal') in common.SAFE_REFUSALS]
    if tags:
        result['refusal'] = tags[-1]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    for mode in ('preflight', 'run', 'recover', 'publish', 'status'):
        group.add_argument('--' + mode, action='store_true')
    parser.add_argument('work', type=Path)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--slot', required=True, type=int, choices=range(1, 15))
    parser.add_argument('--label', help='Existing attempt label, only for recovery, publication or status')
    parser.add_argument('--secret-fd', type=int, default=0)
    args = parser.parse_args(argv)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    common.need(sys.platform == 'linux' and common.WORKER.is_file(), 'test_env_host_required')
    work = common.private_directory(args.work)
    existing = args.recover or args.publish or args.status
    common.need(existing == bool(args.label), 'existing_label_required_only_for_existing_attempt')
    if args.run or args.recover:
        common.need(HERE == work / 'plan', 'execute_frozen_host_for_device_operations')
    plan = checked_plan(work, args.owner, payload=not existing)
    label = args.label if existing else secrets.token_hex(8)
    visit = binding(plan['cohort'], args.owner, args.slot, label)
    with common.work_gate(work):
        visits = work / 'visits'
        for directory in (visits, work / 'receipts'):
            directory.mkdir(mode=0o700, exist_ok=True)
            common.private_directory(directory)
        attempt = visits / f'{args.slot:02}-{label}'
        if existing:
            common.private_directory(attempt)
            common.need(read_json(attempt / 'attempt.json') == visit, 'unbound_attempt')
            report = read_json(attempt / 'operations.json')
            if args.status:
                print(json.dumps(brief(report)))
                return 0
            if args.publish:
                result = publish(work, visit, attempt, report)
                print(json.dumps({'slot': args.slot, 'publication': result}))
                return 0 if result == 'measured' else 2
            common.need(report.get('released') is False and report.get('recovery_required') is True
                        and report.get('identity') is not None and report.get('transport_unreaped') is not True,
                        'manual_reconciliation_required')
        else:
            next_slot(work, plan['cohort'], args.owner, args.slot)
        lease = common.load_module('jpeg_native_lease', work / 'plan/tools/device_lease.py')
        ssh = common.load_module('jpeg_native_ssh', work / 'plan/tools/run_hardware_smoke.py')
        lease.PROJECT = 'Aidoku'
        with common.askpass_environment():
            ssh_argv = ssh.ssh_argv(common.AUTH / 'ssh_config', 'kindle-physical', common.AUTH / 'known_hosts')
        if not args.recover and os.path.lexists(common.LEASE):
            print(json.dumps({'slot': args.slot, 'case': visit['case'], 'lease_requested': False, 'state': 'busy'}))
            return 3
        if args.preflight:
            print(json.dumps({'slot': args.slot, 'case': visit['case'], 'lease_requested': False, 'state': 'ready'}))
            return 0
        retained = []
        secret = rendered.input_secret(args.secret_fd)
        remote = None
        try:
            remote = Transport(ssh_argv, work / 'plan', visit, secret, recover=args.recover)
            request = lease.LeaseRequest(common.LEASE, attempt / 'token', args.owner, PREFIX + label)
            if args.recover:
                report = common.recover(lease=lease, request=request, remote=remote, report=report,
                    save=lambda value: common.checkpoint(attempt / 'operations.json', value))
            else:
                attempt.mkdir(mode=0o700)
                common.checkpoint(attempt / 'attempt.json', visit)
                print(json.dumps({'slot': args.slot, 'case': visit['case'], 'label': label}), flush=True)
                report = operate(lease, request, remote, visit, attempt, retained)
        finally:
            try:
                if remote is not None:
                    remote.close()
            finally:
                secret[:] = b'\0' * len(secret)
        summary = brief(report)
        if report.get('released') is True:
            archive_after_return(visit, attempt, report, retained)
            summary['publication'] = publish(work, visit, attempt, report)
        print(json.dumps(summary))
        if untouched_collision(report):
            return 3
        return 0 if summary.get('publication') == 'measured' or args.recover and report.get('released') else 2


if __name__ == '__main__':
    for signum in common.SIGNALS:
        signal.signal(signum, common.interrupted)
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, common.Stop) else 'operation_failed'}), file=sys.stderr)
        raise SystemExit(1)
