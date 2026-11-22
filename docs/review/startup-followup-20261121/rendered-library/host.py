#!/usr/bin/env python3
"""Run a bounded, log-only stock Library batch on test-env."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import secrets
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
from time import monotonic
import zlib


HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / 'common.py' if (HERE / 'common.py').is_file() else HERE.parent / 'stock-pilot/host.py'
spec = importlib.util.spec_from_file_location('rendered_library_common', COMMON_PATH)
common = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = common
spec.loader.exec_module(common)

CASES = ('control-01', 'control-02', 'control-03')
RUN_PREFIX = 'aidoku-rendered-'
REPLIES = {'guard': b'guard_admitted\n', 'admit': b'render_admitted\n',
           'prepare': b'render_prepared\n', 'visit': b'render_completed\n',
           'close': b'render_closed\n', 'absent': b'render_absent\n'}
TIMEOUTS = {'guard': 25, 'admit': 25, 'prepare': 240, 'visit': 150,
            'collect': 45, 'close': 180, 'absent': 25}
MAX_ARCHIVE = 16 * 1024
MAX_TAR = 64 * 1024
MEMBERS = frozenset(('origin', 'anchor', 'pointer-brackets', 'qualified', 'rendered-library'))
ANCHOR = re.compile(rb'([1-9][0-9]{0,19}) ([1-9][0-9]{0,19}) '
                    rb'([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})\n')
RENDER = re.compile(rb'ARL1 ([1-9][0-9]{0,19}) ([1-9][0-9]{0,19}) '
                    rb'([1-9][0-9]{0,19}) (MATCH|DIFFER) (MATCH|DIFFER)\n')
INPUT_NAME = re.compile(r'input/(?:[a-z][a-z0-9.-]{1,48}|lib/[a-zA-Z0-9_+.\-]{1,80}|'
                        r'models/(?:eng|chi_sim)\.traineddata)')
common.REPLIES = REPLIES
common.SAFE_REFUSALS |= frozenset(('missing_navigation', 'navigation_idle_guard', 'pointer_intent',
    'visit_intent', 'render_window_ms', 'missing_render_logger', 'render_watch_start',
    'render_watch_initial', 'render_watch_timeout', 'render_watch_scan',
    'render_watch_rule_layout', 'render_watch_rule', 'render_watch_runtime',
    'pretrigger_dwell', 'rendered_library', 'visit_close', 'visit_owner',
    'tapcheck_control', 'new_media', 'new_state', 'close_intent', 'cleanup', 'guard_projection',
    'guard_exists', 'guard_save', 'guard_state_changed', 'guard_lights_changed', 'guard_capture',
    'guard_restore', 'missing_render_rule'))


def input_sources(legacy, reference, navigation, guard, visit, dispatch, watcher):
    tap = common.unique_json(common.read_small(navigation / 'tap-files.json', 64 * 1024))
    common.need(common.REQUIRED_INPUT <= tap.keys(), 'missing_tap_input')
    for name, value in tap.items():
        common.need(INPUT_NAME.fullmatch(name) is not None and isinstance(value, str), 'invalid_tap_input')
        path = common.regular(Path(value), 96 * 1024**2)
        if name == 'input/artwork.guard':
            common.need(path == navigation / 'artwork.guard', 'unbound_artwork')
        else:
            common.need(path.is_relative_to(legacy / 'operator/input'), 'unbound_input_tool')
    tap['input/dispatch.sh'] = str(common.regular(dispatch, 128 * 1024))
    sources = {name: Path(value) for name, value in tap.items()}
    sources.update({'guard/guard.sh': common.regular(guard, 128 * 1024),
                    'visit/visit.sh': common.regular(visit, 128 * 1024),
                    'input/render-watch': common.regular(watcher, 2 * 1024**2),
                    'input/reference.rule': common.regular(reference / 'rule', 16 * 1024),
                    'control/program-modes': common.regular(legacy / 'payload/control/program-modes', 1024),
                    'control/launch.sh': common.regular(legacy / 'payload/control/launch.sh', 128 * 1024),
                    'control/app/aidoku': common.regular(legacy / 'payload/control/app/aidoku', 96 * 1024**2),
                    'control/app/aidoku-startup-portrait': common.regular(
                        legacy / 'payload/control/app/aidoku-startup-portrait', 1024**2)})
    rule = common.read_small(sources['input/reference.rule'], 16 * 1024)
    header, separator, points = rule.partition(b'\n')
    common.need(separator == b'\n' and re.fullmatch(rb'AFR3RULE 1 1872 4960 (?:0|180) 6 [1-9][0-9]*', header)
                is not None and len(points) == 4466, 'invalid_reference_rule')
    return sources


def payload_mode(name):
    return 0o600 if name == 'input/reference.rule' else 0o755


def make_payload(target, sources):
    with tarfile.open(target, 'x:gz', compresslevel=2, format=tarfile.USTAR_FORMAT) as archive:
        for name, path in sorted(sources.items()):
            path = common.regular(path, 96 * 1024**2)
            member = tarfile.TarInfo(name)
            member.size, member.mode = path.stat().st_size, payload_mode(name)
            with path.open('rb') as source:
                archive.addfile(member, source)


def verify_payload(target, sources):
    remaining = set(sources)
    common.regular(target, common.MAX_PAYLOAD)
    with tarfile.open(target, 'r|gz') as archive:
        for item in archive:
            common.need(item.name in remaining and item.isreg() and not item.sparse
                        and item.mode == payload_mode(item.name), 'unexpected_payload_member')
            path = common.regular(sources[item.name], 96 * 1024**2)
            common.need(item.size == path.stat().st_size, 'changed_payload_source')
            with path.open('rb') as expected:
                common.need(common.same_bytes(expected, archive.extractfile(item)), 'changed_payload_source')
            remaining.remove(item.name)
    common.need(not remaining, 'missing_payload_member')


def freeze(work, legacy, reference, navigation, owner, device, guard, approved_guard, visit, dispatch, watcher):
    work, legacy = common.private_directory(work), common.private_directory(legacy)
    owner = common.owner_id(owner)
    reference, navigation = Path(reference), Path(navigation)
    common.closed_reference(reference, 'reference', legacy)
    common.closed_reference(navigation, 'navigation', legacy)
    common.need(common.same_file(guard, approved_guard), 'guard_differs_from_approved')
    sources = input_sources(legacy, reference, navigation, guard, visit, dispatch, watcher)
    target = work / 'plan'
    target.mkdir(mode=0o700)
    label = secrets.token_hex(8)
    for name, source in (('host.py', Path(__file__).resolve()), ('common.py', COMMON_PATH.resolve()),
                         ('device.sh', device), ('guard.sh', guard)):
        common.write_new(target / name, common.read_small(source, 1024**2))
    os.chmod(target / 'host.py', 0o700)
    os.chmod(target / 'common.py', 0o700)
    tools = target / 'tools'
    tools.mkdir(mode=0o700)
    for name in ('device_lease.py', 'run_hardware_smoke.py'):
        common.write_new(tools / name, common.read_small(legacy / 'tools' / name, 1024**2))
    make_payload(target / 'payload.tar.gz', sources)
    verify_payload(target / 'payload.tar.gz', sources)
    plan = {'schema': 1, 'label': label, 'owner': owner, 'origin': 'stock-tap', 'cases': list(CASES),
            'sources': {name: str(path) for name, path in sources.items()},
            'reference': str(reference), 'navigation': str(navigation), 'legacy': str(legacy),
            'device': str(device), 'guard': str(guard), 'approved_guard': str(approved_guard),
            'visit': str(visit), 'dispatch': str(dispatch), 'watcher': str(watcher),
            'host_source': str(Path(__file__).resolve()), 'common_source': str(COMMON_PATH.resolve())}
    common.write_new(target / 'plan.json', (json.dumps(plan, indent=2) + '\n').encode())
    common.write_new(target / 'ready', b'ready\n')
    return label


def checked_plan(work, owner, *, minimal=False):
    plan_dir = common.private_directory(work / 'plan')
    common.need(common.read_small(plan_dir / 'ready', 16) == b'ready\n', 'plan_not_ready')
    plan = common.unique_json(common.read_small(plan_dir / 'plan.json', 128 * 1024))
    common.need(plan.get('schema') == 1 and plan.get('origin') == 'stock-tap' and plan.get('cases') == list(CASES)
                and plan.get('owner') == common.owner_id(owner) and isinstance(plan.get('label'), str)
                and re.fullmatch(r'[0-9a-f]{16}', plan['label']), 'invalid_plan')
    for name in ('host.py', 'common.py', 'device.sh', 'guard.sh',
                 'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
        common.read_small(plan_dir / name)
    if minimal:
        return plan
    legacy = common.private_directory(Path(plan['legacy']))
    reference, navigation = Path(plan['reference']), Path(plan['navigation'])
    common.closed_reference(reference, 'reference', legacy)
    common.closed_reference(navigation, 'navigation', legacy)
    common.need(common.same_file(Path(plan['guard']), Path(plan['approved_guard'])), 'guard_differs_from_approved')
    sources = input_sources(legacy, reference, navigation,
                            *(Path(plan[key]) for key in ('guard', 'visit', 'dispatch', 'watcher')))
    common.need(plan.get('sources') == {name: str(path) for name, path in sources.items()}, 'changed_source_plan')
    verify_payload(plan_dir / 'payload.tar.gz', sources)
    for name, key in (('host.py', 'host_source'), ('common.py', 'common_source'),
                      ('device.sh', 'device'), ('guard.sh', 'guard')):
        common.need(common.same_file(plan_dir / name, Path(plan[key])), 'changed_frozen_operator')
    for name in ('device_lease.py', 'run_hardware_smoke.py'):
        common.need(common.same_file(plan_dir / 'tools' / name, legacy / 'tools' / name), 'changed_frozen_tool')
    return plan


class Transport(common.Transport):
    def __call__(self, operation):
        kind, separator, case = operation.partition(':')
        common.need(kind in TIMEOUTS and ((kind in ('visit', 'collect') and separator and case in CASES)
                    or (kind not in ('visit', 'collect') and not separator)), 'invalid_operation')
        started = monotonic()
        stats = {'stdout_bytes': 0, 'stderr_bytes': 0, 'stderr_truncated': False, 'elapsed_ms': 0}
        self.last_stats = stats
        body = self.guard if kind == 'guard' else self.device
        descriptor = self.fds[1] if kind == 'guard' else self.fds[2] if kind == 'prepare' else self.fds[0]
        args = {'guard': ['admit'], 'admit': ['admit'], 'prepare': ['prepare', 'tapcheck'],
                'visit': ['visit', case], 'collect': ['collect', case], 'close': ['close'],
                'absent': ['absent']}[kind]
        command = f'exec sh -c "$(dd bs=1 count={len(body)} 2>/dev/null)" sh ' + shlex.join(
            [args[0], self.label, *args[1:]])
        secret_fd, child = None, None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            with common.blocked_signals():
                secret_fd = common.secret_pipe(self.secret)
            env = os.environ.copy()
            env.update(SSH_ASKPASS=str(Path(common.__file__).resolve()), SSH_ASKPASS_REQUIRE='force',
                       AIDOKU_PILOT_ASKPASS='1', AIDOKU_PILOT_PARENT=str(os.getpid()),
                       AIDOKU_PILOT_FD=str(secret_fd), LC_ALL='C')
            try:
                with common.blocked_signals():
                    child = subprocess.Popen([*self.argv, command], stdin=descriptor, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, start_new_session=True, env=env)
            except OSError:
                raise common.Stop('transport_spawn_error') from None
            outputs = [bytearray(), bytearray()]
            deadline = monotonic() + TIMEOUTS[kind]
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
                while selector.get_map():
                    ready = selector.select(max(0, deadline - monotonic()))
                    common.need(ready and monotonic() < deadline, 'transport_timeout')
                    for item, _ in ready:
                        if item.data == 'stdout':
                            limit = MAX_ARCHIVE if kind == 'collect' else 4096
                            chunk = os.read(item.fd, min(65536, limit - len(outputs[0]) + 1))
                            stats['stdout_bytes'] += len(chunk)
                            outputs[0].extend(chunk)
                            common.need(len(outputs[0]) <= limit, 'transport_stdout_limit')
                        else:
                            chunk = os.read(item.fd, 65536)
                            stats['stderr_bytes'] += len(chunk)
                            if len(chunk) >= common.STDERR_TAIL:
                                outputs[1][:] = chunk[-common.STDERR_TAIL:]
                            else:
                                excess = len(outputs[1]) + len(chunk) - common.STDERR_TAIL
                                if excess > 0:
                                    del outputs[1][:excess]
                                outputs[1].extend(chunk)
                            stats['stderr_truncated'] = stats['stderr_bytes'] > common.STDERR_TAIL
                        if not chunk:
                            selector.unregister(item.fileobj)
            try:
                code = child.wait(timeout=max(0, deadline - monotonic()))
            except subprocess.TimeoutExpired:
                raise common.Stop('transport_timeout') from None
            return code, bytes(outputs[0]), bytes(outputs[1])
        except (OSError, ValueError):
            raise common.Stop('transport_io_error') from None
        finally:
            try:
                if secret_fd is not None:
                    os.close(secret_fd)
            finally:
                try:
                    if child is not None:
                        with common.blocked_signals():
                            try:
                                try:
                                    os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                child.wait(timeout=10)
                            except (Exception, KeyboardInterrupt):
                                raise common.TransportUnreaped('transport_not_reaped') from None
                            finally:
                                child.stdout.close()
                                child.stderr.close()
                finally:
                    stats['elapsed_ms'] = max(0, int((monotonic() - started) * 1000))


def validate_members(members, case):
    common.need(set(members) == MEMBERS and members['origin'] == b'stock-tap\n'
                and members['qualified'] == (case + ' stock-tap\n').encode(), 'unbound_evidence')
    common.need(members['anchor'] == members['pointer-brackets'], 'pointer_brackets_differ')
    anchor, rendered = ANCHOR.fullmatch(members['anchor']), RENDER.fullmatch(members['rendered-library'])
    common.need(anchor is not None and rendered is not None, 'invalid_text_evidence')
    numbers = tuple(map(int, (*anchor.groups(), *rendered.groups()[:3])))
    common.need(all(value <= 2**64 - 1 for value in numbers), 'invalid_text_evidence')
    return numbers, tuple(value.decode('ascii') for value in rendered.groups()[3:])


def archive_members(data, case):
    common.need(case in CASES and isinstance(data, bytes) and 10 <= len(data) <= MAX_ARCHIVE,
                'invalid_evidence_size')
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(data, MAX_TAR + 1)
        common.need(len(raw) <= MAX_TAR and len(raw) % 512 == 0 and decoder.eof and not decoder.unused_data
                    and not decoder.unconsumed_tail, 'invalid_archive')
        members, offset = {}, 0
        while offset < len(raw):
            block = raw[offset:offset + 512]
            common.need(len(block) == 512, 'invalid_archive')
            if not any(block):
                common.need(len(raw) - offset >= 1024 and not any(raw[offset:]), 'invalid_archive')
                break
            item = tarfile.TarInfo.frombuf(block, 'utf-8', 'strict')
            prefix = 'out/' + case + '/'
            common.need(item.name.startswith(prefix), 'unexpected_evidence_member')
            name = item.name[len(prefix):]
            common.need(name in MEMBERS and name not in members and item.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                        and not item.sparse and 0 < item.size <= 256, 'unexpected_evidence_member')
            start = offset + 512
            end = start + item.size
            common.need(end <= len(raw), 'invalid_archive')
            members[name] = raw[start:end]
            offset = start + ((item.size + 511) // 512) * 512
            common.need(offset <= len(raw) and not any(raw[end:offset]), 'invalid_archive')
        else:
            raise common.Stop('invalid_archive')
        validate_members(members, case)
        return members
    except (zlib.error, tarfile.TarError, UnicodeError, ValueError):
        raise common.Stop('invalid_archive') from None


def save_case(attempt, case, members):
    validate_members(members, case)
    record = {'schema': 1, 'case': case, 'members': {name: value.decode('ascii') for name, value in members.items()}}
    common.write_new(attempt / (case + '.json'), (json.dumps(record, sort_keys=True) + '\n').encode())


def input_secret_fd(descriptor):
    common.need(type(descriptor) is int and 0 <= descriptor <= 2**31 - 1, 'private_secret_pipe_required')
    try:
        info = os.fstat(descriptor)
    except OSError:
        raise common.Stop('private_secret_pipe_required') from None
    common.need(not os.isatty(descriptor) and (stat.S_ISFIFO(info.st_mode) or stat.S_ISSOCK(info.st_mode)),
                'private_secret_pipe_required')
    secret = bytearray()
    try:
        while len(secret) <= common.SECRET_LIMIT:
            value = os.read(descriptor, 1)
            if not value or value == b'\n':
                break
            common.need(value not in (b'\0', b'\r'), 'invalid_password')
            secret.extend(value)
        common.need(0 < len(secret) <= common.SECRET_LIMIT, 'invalid_password')
        return secret
    except BaseException:
        secret[:] = b'\0' * len(secret)
        raise


def input_secret(descriptor):
    if descriptor == 0 and os.isatty(descriptor):
        return common.input_secret()
    return input_secret_fd(descriptor)


def operate(*, lease, request, remote, save, save_evidence):
    report = {'schema': 1, 'request': {'owner_thread': request.owner_thread, 'run_id': request.run_id},
              'cases': list(CASES), 'completed': [], 'operations': [], 'phase': 'acquire', 'identity': None,
              'released': False, 'recovery_required': True, 'operations_passed': False, 'mutation_possible': False}
    held, mutation, close_confirmed, durable, transport_reaped = False, False, False, True, True

    def mark():
        nonlocal durable
        try:
            save(report)
        except (Exception, KeyboardInterrupt):
            durable = False
            raise common.Stop('checkpoint_failed') from None

    def call(operation):
        nonlocal transport_reaped
        common.owned(lease, request, report['identity'])
        report['phase'] = operation
        mark()
        try:
            status, stdout, stderr = remote(operation)
        except common.TransportUnreaped:
            transport_reaped = False
            report['transport_unreaped'] = True
            report['operations'].append({'operation': operation, 'outcome': 'transport_not_reaped'})
            raise common.Stop('transport_not_reaped') from None
        except (Exception, KeyboardInterrupt) as error:
            report['operations'].append(common.unknown_operation(operation, error, remote))
            raise common.Stop(operation + '_' + common.transport_reason(error)) from None
        kind = operation.partition(':')[0]
        good = type(status) is int and status == 0 and isinstance(stdout, bytes) and isinstance(stderr, bytes)
        if kind == 'collect':
            good = good and 10 <= len(stdout) <= MAX_ARCHIVE and stdout.startswith(b'\x1f\x8b\x08')
        else:
            good = good and stdout == REPLIES[kind]
        report['operations'].append(common.operation_result(operation, good, status, stderr, remote))
        mark()
        common.need(good, operation + '_failed')
        return stdout

    try:
        mark()
        try:
            with common.blocked_signals():
                proof = lease.acquire(request)
                held = True
        except (Exception, KeyboardInterrupt) as error:
            details = getattr(error, 'details', None)
            untouched = ('lease_directory_created', 'private_token_created', 'owner_record_created')
            if (isinstance(getattr(lease, 'LeaseError', None), type) and isinstance(error, lease.LeaseError)
                    and getattr(error, 'code', None) in ('lease_exists', 'operation_busy')
                    and isinstance(details, dict) and all(details.get(key) is False for key in untouched)):
                report['error'], report['recovery_required'] = 'lease_busy', False
            else:
                report['error'] = 'acquisition_unknown'
            mark()
            return report
        common.need(proof.get('ok') is True and proof.get('operation') == 'acquire' and proof.get('state') == 'held',
                    'acquisition_unverified')
        report['identity'] = common.checked_identity(proof, request)
        report['acquisition'] = proof
        mark()
        call('guard')
        call('admit')
        mutation = True
        report['mutation_possible'] = True
        call('prepare')
        for case in CASES:
            call('visit:' + case)
            members = archive_members(call('collect:' + case), case)
            save_evidence(case, members)
            report['completed'].append(case)
            mark()
    except (Exception, KeyboardInterrupt) as error:
        report['error'] = str(error) if isinstance(error, common.Stop) else 'operation_interrupted'
    finally:
        if held and report['identity'] is not None:
            if mutation and transport_reaped:
                pending = None
                try:
                    common.owned(lease, request, report['identity'])
                    report['phase'] = 'close'
                    try:
                        mark()
                    except common.Stop:
                        pass
                    pending = 'close'
                    status, stdout, stderr = remote('close')
                    pending = None
                    close_confirmed = (type(status) is int and status == 0 and stdout == REPLIES['close']
                                       and isinstance(stderr, bytes))
                    report['operations'].append(common.operation_result('close', close_confirmed, status, stderr, remote))
                    if not close_confirmed:
                        report['close_error'] = 'close_failed'
                    if close_confirmed:
                        report['close_confirmed'] = True
                        close_confirmed = False
                        try:
                            mark()
                        except common.Stop:
                            pass
                        common.owned(lease, request, report['identity'])
                        report['phase'] = 'absent'
                        try:
                            mark()
                        except common.Stop:
                            pass
                        pending = 'absent'
                        status, stdout, stderr = remote('absent')
                        pending = None
                        absent = (type(status) is int and status == 0 and stdout == REPLIES['absent']
                                  and isinstance(stderr, bytes))
                        report['operations'].append(common.operation_result('absent', absent, status, stderr, remote))
                        if not absent:
                            report['close_error'] = 'absence_failed'
                        close_confirmed = absent
                        if absent:
                            report['absence_confirmed'] = True
                            try:
                                mark()
                            except common.Stop:
                                pass
                except common.TransportUnreaped:
                    transport_reaped = False
                    report['transport_unreaped'] = True
                    report['close_error'] = 'transport_not_reaped'
                    if pending is not None:
                        report['operations'].append({'operation': pending, 'outcome': 'transport_not_reaped'})
                except (Exception, KeyboardInterrupt) as error:
                    if pending is not None:
                        report['operations'].append(common.unknown_operation(pending, error, remote))
                        report['close_error'] = pending + '_' + common.transport_reason(error)
                    else:
                        report['close_error'] = 'close_unknown'
            elif mutation:
                report['close_error'] = 'transport_not_reaped'
            else:
                close_confirmed = True
            if (close_confirmed and durable and transport_reaped
                    and (not mutation or report.get('absence_confirmed') is True)):
                try:
                    report['phase'] = 'release'
                    mark()
                    report['release'] = common.release_owned(lease, request, report['identity'])
                    report['released'], report['recovery_required'] = True, False
                    report['operations_passed'] = report['completed'] == list(CASES) and 'error' not in report
                except (Exception, KeyboardInterrupt):
                    report['release_error'] = 'release_unverified'
        try:
            mark()
        except common.Stop:
            report['error'] = 'checkpoint_failed'
    return report


def verified_report(work, plan):
    attempt = common.private_directory(work / 'attempt')
    report = common.unique_json(common.read_small(attempt / 'operations.json', 256 * 1024))
    expected_request = {'owner_thread': plan['owner'], 'run_id': RUN_PREFIX + plan['label']}
    identity, acquired, release = report.get('identity'), report.get('acquisition'), report.get('release')
    operations, completed = report.get('operations'), report.get('completed')
    common.need(report.get('schema') == 1 and report.get('request') == expected_request
                and report.get('cases') == list(CASES) and isinstance(completed, list)
                and completed == list(CASES[:len(completed)]) and 0 < len(completed) <= len(CASES)
                and report.get('released') is True and report.get('recovery_required') is False
                and report.get('close_confirmed') is True and report.get('absence_confirmed') is True
                and isinstance(identity, dict) and isinstance(acquired, dict) and isinstance(release, dict)
                and all(isinstance(identity.get(key), str) and identity[key]
                        and acquired.get(key) == identity[key] and release.get(key) == identity[key]
                        for key in common.IDENTITY)
                and identity.get('project') == 'Aidoku' and identity.get('owner_thread') == plan['owner']
                and identity.get('run_id') == expected_request['run_id']
                and acquired.get('ok') is True and acquired.get('operation') == 'acquire'
                and acquired.get('state') == 'held' and release.get('ok') is True
                and release.get('operation') == 'release' and release.get('state') == 'released'
                and all(release.get(key) is True for key in ('owner_and_token_checked',
                    'lease_directory_absent_after_release', 'native_restored_and_connections_closed'))
                and isinstance(operations, list) and all(isinstance(item, dict) for item in operations),
                'unverified_visit_or_release')
    first_restore = next((index for index, item in enumerate(operations)
                          if item.get('operation') in ('close', 'absent')), len(operations))
    activity, restoration = operations[:first_restore], operations[first_restore:]
    expected = ['guard', 'admit', 'prepare']
    for case in completed:
        expected.extend(('visit:' + case, 'collect:' + case))
    common.need([item.get('operation') for item in activity[:len(expected)]] == expected
                and all(item.get('outcome') == 'ok' for item in activity[:len(expected)])
                and all(item.get('operation') in ('close', 'absent') for item in restoration),
                'unverified_visit_or_release')
    remaining = [item.get('operation') for item in activity[len(expected):]]
    if len(completed) < len(CASES):
        next_case = CASES[len(completed)]
        common.need(remaining in ([], ['visit:' + next_case],
                    ['visit:' + next_case, 'collect:' + next_case]), 'unverified_visit_or_release')
    else:
        common.need(not remaining, 'unverified_visit_or_release')
    good_close = [index for index, item in enumerate(restoration)
                  if item.get('operation') == 'close' and item.get('outcome') == 'ok']
    good_absent = [index for index, item in enumerate(restoration)
                   if item.get('operation') == 'absent' and item.get('outcome') == 'ok']
    common.need(good_close and good_absent and good_close[-1] < good_absent[-1], 'unverified_visit_or_release')
    return attempt, report


def measured_case(case, members):
    numbers, covers = validate_members(members, case)
    down_before, down_after, up_before, up_after, last_negative, first_match, confirmed = numbers
    common.need(down_before <= down_after < up_before <= up_after
                and down_after - down_before <= 100_000 and up_after - up_before <= 100_000
                and up_before - down_after >= 50_000 and last_negative < first_match
                and first_match >= up_after and confirmed - first_match >= 200_000,
                'invalid_observation_brackets')
    upper = first_match - up_before
    lower = last_negative - up_after if last_negative >= up_after else None
    return {'case': case, 'upper_bound_us': upper, 'upper_bound_ms': upper / 1000,
            'lower_bound_us': lower, 'lower_bound_ms': None if lower is None else lower / 1000,
            'cover_first_vs_reference': covers[0], 'cover_confirmed_vs_reference': covers[1]}


def analyze(work, plan):
    attempt, report = verified_report(work, plan)
    results = []
    for case in report['completed']:
        try:
            record = common.unique_json(common.read_small(attempt / (case + '.json'), 4096))
            common.need(record.get('schema') == 1 and record.get('case') == case
                        and set(record) == {'schema', 'case', 'members'} and isinstance(record.get('members'), dict)
                        and all(isinstance(value, str) for value in record['members'].values()), 'invalid_case_record')
            members = {name: value.encode('ascii') for name, value in record['members'].items()}
            results.append(measured_case(case, members))
        except (common.Stop, OSError, UnicodeError, ValueError) as error:
            reason = str(error) if isinstance(error, common.Stop) else 'invalid_case_record'
            results.append({'case': case, 'measurement': 'not_observed', 'reason': reason})
    valid = [item for item in results if 'upper_bound_us' in item]
    complete = len(valid) == len(CASES) and report['completed'] == list(CASES)
    median = sorted(item['upper_bound_us'] for item in valid)[1] if complete else None
    reason = report.get('error')
    if not isinstance(reason, str) or re.fullmatch(r'[a-z0-9_:.-]{1,120}', reason) is None:
        reason = None
    refusals = [item.get('refusal') for item in report['operations'] if item.get('refusal')]
    output = {'ok': bool(valid), 'released': True, 'clock': 'CLOCK_BOOTTIME',
              'endpoint': 'sampled_noncover_library_match', 'start': 'stock_tile_release_bracket',
              'planned_cases': list(CASES), 'completed_cases': report['completed'], 'complete_batch': complete,
              'stop_reason': reason, 'refusal': refusals[-1] if refusals else None,
              'cases': results, 'median_upper_bound_us': median,
              'median_upper_bound_ms': None if median is None else median / 1000}
    data = (json.dumps(output, indent=2, allow_nan=False) + '\n').encode()
    target = attempt / 'measurement.json'
    if os.path.lexists(target):
        common.need(common.read_small(target, 64 * 1024) == data, 'measurement_already_recorded')
    else:
        common.write_new(target, data)
    print(json.dumps(output, allow_nan=False))
    return 0 if output['ok'] else 2


def status_report(work):
    status = common.status_report(work)
    if status.get('attempt') == 'recorded':
        report = common.unique_json(common.read_small(work / 'attempt/operations.json', 256 * 1024))
        completed = report.get('completed')
        if isinstance(completed, list) and completed == list(CASES[:len(completed)]):
            status['completed_cases'] = completed
        for key in ('error', 'close_error', 'release_error', 'recovery_error'):
            value = report.get(key)
            if isinstance(value, str) and re.fullmatch(r'[a-z0-9_:.-]{1,120}', value):
                status[key] = value
        refusals = [item.get('refusal') for item in report.get('operations', [])
                    if isinstance(item, dict) and item.get('refusal') in common.SAFE_REFUSALS]
        if refusals:
            status['refusal'] = refusals[-1]
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ('freeze', 'preflight', 'run', 'recover', 'analyze', 'status'):
        actions.add_argument('--' + name, action='store_true')
    parser.add_argument('work', type=Path)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--secret-fd', type=int, default=0)
    for name in ('legacy', 'reference', 'navigation', 'device', 'guard', 'approved-guard', 'visit', 'dispatch',
                 'watcher'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    common.need(sys.platform == 'linux' and common.WORKER.is_file(), 'test_env_host_required')
    work, owner = common.private_directory(args.work), common.owner_id(args.owner)
    if args.freeze:
        names = ('legacy', 'reference', 'navigation', 'device', 'guard', 'approved_guard', 'visit', 'dispatch',
                 'watcher')
        common.need(all(getattr(args, name) is not None for name in names), 'freeze_inputs_required')
        with common.work_gate(work):
            label = freeze(work, args.legacy, args.reference, args.navigation, owner, args.device, args.guard,
                           args.approved_guard, args.visit, args.dispatch, args.watcher)
        print(json.dumps({'frozen': True, 'label': label, 'cases': list(CASES), 'lease_requested': False}))
        return 0
    plan = checked_plan(work, owner, minimal=args.recover or args.analyze or args.status)
    if args.status:
        print(json.dumps(status_report(work)))
        return 0
    if args.analyze:
        return analyze(work, plan)
    if not args.recover:
        common.need(not os.path.lexists(work / 'attempt'), 'plan_already_used')
    lease = common.load_module('rendered_library_lease', work / 'plan/tools/device_lease.py')
    ssh = common.load_module('rendered_library_ssh', work / 'plan/tools/run_hardware_smoke.py')
    lease.PROJECT = 'Aidoku'
    common.need(os.access(Path(common.__file__).resolve(), os.X_OK), 'askpass_script_must_be_executable')
    with common.askpass_environment():
        ssh_argv = ssh.ssh_argv(common.AUTH / 'ssh_config', 'kindle-physical', common.AUTH / 'known_hosts')
    if args.preflight:
        try:
            common.live_lease_gate()
            lease_free = True
        except common.Stop as error:
            if str(error) != 'lease_busy':
                raise
            lease_free = False
        print(json.dumps({'ready': lease_free, 'package_verified': True, 'ssh_pin_configured_offline': True,
                          'live_lease_free': lease_free, 'unused_plan': True, 'lease_requested': False}))
        return 0 if lease_free else 3
    with common.work_gate(work):
        attempt = work / 'attempt'
        if args.recover:
            common.private_directory(attempt)
            report = common.unique_json(common.read_small(attempt / 'operations.json', 256 * 1024))
            common.need(report.get('schema') == 1 and report.get('released') is False
                        and report.get('recovery_required') is True and report.get('transport_unreaped') is not True
                        and report.get('request') == {'owner_thread': owner, 'run_id': RUN_PREFIX + plan['label']}
                        and isinstance(report.get('identity'), dict), 'manual_reconciliation_required')
        else:
            common.need(not os.path.lexists(attempt), 'plan_already_used')
            print('Automatic stock-card batch; do not touch the device.', flush=True)
        secret = input_secret(args.secret_fd)
        remote = None
        try:
            remote = Transport(ssh_argv, work / 'plan', plan['label'], secret, recovery=args.recover)
            request = lease.LeaseRequest(common.LEASE, attempt / 'token', owner, RUN_PREFIX + plan['label'])
            if args.recover:
                report = common.recover(lease=lease, request=request, remote=remote, report=report,
                                        save=lambda value: common.checkpoint(attempt / 'operations.json', value))
            else:
                common.live_lease_gate()
                attempt.mkdir(mode=0o700)
                report = operate(lease=lease, request=request, remote=remote,
                                 save=lambda value: common.checkpoint(attempt / 'operations.json', value),
                                 save_evidence=lambda case, value: save_case(attempt, case, value))
        finally:
            try:
                if remote is not None:
                    remote.close()
            finally:
                secret[:] = b'\0' * len(secret)
    if report.get('released') is True and report.get('completed'):
        return analyze(work, plan)
    refusals = [item.get('refusal') for item in report.get('operations', []) if item.get('refusal')]
    print(json.dumps({'operations_passed': report.get('operations_passed'), 'released': report.get('released'),
                      'recovery_required': report.get('recovery_required'), 'completed_cases': report.get('completed'),
                      'error': report.get('recovery_error') or report.get('error'),
                      'close_error': report.get('close_error'), 'release_error': report.get('release_error'),
                      'refusal': refusals[-1] if refusals else None}))
    return 0 if args.recover and report.get('released') is True else 1


if __name__ == '__main__':
    for signum in common.SIGNALS:
        signal.signal(signum, common.interrupted)
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, common.Stop) else 'operation_failed'}),
              file=sys.stderr)
        raise SystemExit(1)
