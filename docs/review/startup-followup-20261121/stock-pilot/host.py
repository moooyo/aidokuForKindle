#!/usr/bin/env python3
"""Freeze, run, and analyze one separately owned stock-tile pilot on test-env."""

import argparse
from contextlib import contextmanager
import fcntl
import getpass
import importlib.util
import io
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
import tempfile
from time import monotonic
import warnings


HERE = Path(__file__).resolve().parent
LEASE = Path('/tmp/codex-kindle-physical-test.lock')
CLEANUP = Path('/var/tmp/kindle-build-coordination/aidoku-cleanup')
AUTH = Path('/root/.config/kindle-physical')
WORKER = Path('/opt/kindle-toolchain/ubuntu-run')
IDENTITY = ('project', 'owner_thread', 'run_id', 'lease_token_sha256')
SIGNALS = (signal.SIGINT, signal.SIGHUP, signal.SIGTERM)
SECRET_LIMIT = 1024
MAX_PAYLOAD = 256 * 1024**2
MAX_EVIDENCE = 24 * 1024**2
STDERR_TAIL = 64 * 1024
TRANSPORT_ERRORS = frozenset(('transport_timeout', 'transport_stdout_limit', 'transport_io_error',
                              'transport_spawn_error', 'transport_input_failed'))
CASE = 'control-warmup'
REPLIES = {'guard': b'guard_admitted\n', 'admit': b'pilot_admitted\n', 'prepare': b'pilot_prepared\n',
           'pilot': b'pilot_completed\n', 'close': b'pilot_closed\n', 'absent': b'pilot_absent\n'}
TIMEOUTS = {'guard': 25, 'admit': 25, 'prepare': 240, 'pilot': 120, 'collect': 60, 'close': 180, 'absent': 25}
REQUIRED_INPUT = {'input/navigation.sh', 'input/native-target', 'input/card-input',
                  'input/dispatch.sh', 'input/tile-input', 'input/artwork.guard',
                  'input/lib/ld-linux-armhf.so.3', 'input/models/eng.traineddata',
                  'input/models/chi_sim.traineddata'}
SAFE_REFUSALS = frozenset(('guard_admission', 'guard_lights', 'native_admission', 'stage_admission',
    'live_operation', 'previsit_protection', 'postvisit_protection', 'guard_owner', 'changed_control',
    'pilot_intent', 'visit_prepare', 'native_library', 'native_tile',
    'initial_capture', 'visit_arm', 'touch_probe', 'touch_witness_started', 'observer_trace', 'touch_witness',
    'missing_touch', 'tile_dispatch', 'pointer_brackets', 'normal_session', 'normal_exit', 'visit_recovery',
    'pointer_recovery', 'native_recovery', 'close_checkpoint', 'partial_state', 'stage_owner', 'state_owner',
    'stage_present', 'evidence', 'transfer', 'prepare_idle_guard', 'payload', 'missing_modules', 'readiness',
    'watcher_started', 'touch_witness_started', 'selected_library', 'verify_tile', 'tap_not_confirmed',
    'visit_ownership', 'visit_normal_gui', 'visit_native_return', 'visit_owned_exit', 'visit_exit_qualification'))
ANCHOR4 = re.compile(rb'([1-9][0-9]{0,19}) ([1-9][0-9]{0,19}) ([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})\n')
ANCHOR2 = re.compile(rb'([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})\n')


class Stop(Exception):
    pass


class TransportUnreaped(Stop):
    pass


class PilotInterrupted(KeyboardInterrupt):
    def __init__(self, signum):
        self.reason = {signal.SIGINT: 'interrupted_sigint', signal.SIGHUP: 'interrupted_sighup',
                       signal.SIGTERM: 'interrupted_sigterm'}.get(signum, 'interrupted_signal')


def need(condition, reason):
    if not condition:
        raise Stop(reason)


def unique_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, 'duplicate_json_field')
            result[key] = value
        return result
    result = json.loads(raw, object_pairs_hook=unique)
    need(isinstance(result, dict), 'invalid_json_object')
    return result


def private_directory(path):
    path = Path(path)
    info = path.lstat()
    need(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode)
         and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
         'private_directory_required')
    return path


def regular(path, maximum, *, empty=False):
    path = Path(path)
    info = path.lstat()
    need(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISREG(info.st_mode)
         and (empty or info.st_size > 0) and info.st_size <= maximum, 'invalid_input_file')
    return path


def read_small(path, maximum=1024**2):
    return regular(path, maximum).read_bytes()


def same_bytes(left, right):
    while True:
        piece = left.read(256 * 1024)
        if piece != right.read(256 * 1024):
            return False
        if not piece:
            return True


def same_file(left, right):
    with regular(left, 96 * 1024**2).open('rb') as first, regular(right, 96 * 1024**2).open('rb') as second:
        return same_bytes(first, second)


def write_new(path, data):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def checkpoint(path, report):
    data = (json.dumps(report, indent=2, allow_nan=False) + '\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def owner_id(value):
    need(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value),
         'designated_operator_required')
    return value


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, regular(path, 1024**2))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def observer_source():
    local = HERE / 'observer.py'
    if local.is_file():
        return regular(local, 1024**2)
    return regular(HERE.parent / 'startup-native-20261121/observer/observer.py', 1024**2)


def closed_reference(folder, mode, root):
    folder = Path(folder)
    need(folder.parent == root / 'batches' and re.fullmatch(mode + '-[0-9a-f]{16}', folder.name),
         'unbound_reference')
    private_directory(folder)
    receipt = unique_json(read_small(folder / 'receipt.json', 256 * 1024))
    acquired, released = receipt.get('acquisition'), receipt.get('release')
    need(receipt.get('ok') is True and receipt.get('mode') == mode and receipt.get('completed') == [mode]
         and isinstance(acquired, dict) and isinstance(released, dict)
         and acquired.get('ok') is True and acquired.get('operation') == 'acquire'
         and acquired.get('state') == 'held' and acquired.get('project') == 'Aidoku'
         and released.get('ok') is True and released.get('operation') == 'release'
         and released.get('state') == 'released'
         and all(released.get(key) is True for key in ('owner_and_token_checked',
                 'lease_directory_absent_after_release', 'native_restored_and_connections_closed'))
         and all(isinstance(acquired.get(key), str) and acquired[key]
                 and acquired[key] == released.get(key) for key in IDENTITY), 'unclosed_reference')


def input_sources(legacy, navigation, guard, visit, dispatch, witness):
    tap = unique_json(read_small(navigation / 'tap-files.json', 64 * 1024))
    need(REQUIRED_INPUT <= tap.keys(), 'missing_tap_input')
    for name, value in tap.items():
        need(re.fullmatch(r'input/(?:[a-z][a-z0-9.-]{1,48}|lib/[a-zA-Z0-9_+.\-]{1,80}|'
                          r'models/(?:eng|chi_sim)\.traineddata)', name) is not None
             and isinstance(value, str), 'invalid_tap_input')
        path = regular(Path(value), 96 * 1024**2)
        if name == 'input/artwork.guard':
            need(path == navigation / 'artwork.guard', 'unbound_artwork')
        else:
            need(path.is_relative_to(legacy / 'operator' / 'input'), 'unbound_input_tool')
    tap['input/dispatch.sh'] = str(regular(dispatch, 128 * 1024))
    sources = {name: Path(value) for name, value in tap.items()}
    sources.update({'guard/guard.sh': regular(guard, 128 * 1024),
                    'visit/visit.sh': regular(visit, 128 * 1024),
                    'observer/framewatch': regular(legacy / 'operator/observer/framewatch', 1024**2),
                    'input/touch-witness': regular(witness, 2 * 1024**2),
                    'control/program-modes': regular(legacy / 'payload/control/program-modes', 1024),
                    'control/launch.sh': regular(legacy / 'payload/control/launch.sh', 128 * 1024),
                    'control/app/aidoku': regular(legacy / 'payload/control/app/aidoku', 96 * 1024**2),
                    'control/app/aidoku-startup-portrait': regular(
                        legacy / 'payload/control/app/aidoku-startup-portrait', 1024**2)})
    return sources


def make_payload(target, sources):
    with tarfile.open(target, 'x:gz', compresslevel=2, format=tarfile.USTAR_FORMAT) as archive:
        for name, path in sorted(sources.items()):
            path = regular(path, 96 * 1024**2)
            member = tarfile.TarInfo(name)
            member.size, member.mode = path.stat().st_size, 0o755
            with path.open('rb') as source:
                archive.addfile(member, source)


def verify_payload(target, sources):
    remaining = set(sources)
    regular(target, MAX_PAYLOAD)
    with tarfile.open(target, 'r|gz') as archive:
        for item in archive:
            need(item.name in remaining and item.isreg() and not item.sparse and item.mode == 0o755,
                 'unexpected_payload_member')
            path = regular(sources[item.name], 96 * 1024**2)
            need(item.size == path.stat().st_size, 'changed_payload_source')
            with path.open('rb') as expected:
                need(same_bytes(expected, archive.extractfile(item)), 'changed_payload_source')
            remaining.remove(item.name)
    need(not remaining, 'missing_payload_member')


def freeze(work, legacy, reference, navigation, owner, device, guard, approved_guard, visit, dispatch, witness):
    work, legacy = private_directory(work), private_directory(legacy)
    owner = owner_id(owner)
    reference, navigation = Path(reference), Path(navigation)
    closed_reference(reference, 'reference', legacy)
    closed_reference(navigation, 'navigation', legacy)
    need(same_file(guard, approved_guard), 'guard_differs_from_approved')
    sources = input_sources(legacy, navigation, guard, visit, dispatch, witness)
    rule = read_small(reference / 'rule', 16 * 1024)
    target = work / 'plan'
    target.mkdir(mode=0o700)
    label = secrets.token_hex(8)
    body = read_small(device, 128 * 1024)
    write_new(target / 'device.sh', body)
    write_new(target / 'guard.sh', read_small(guard, 128 * 1024))
    write_new(target / 'rule', rule)
    for name in ('host.py', 'pilot_pixels.py'):
        write_new(target / name, read_small(HERE / name, 1024**2))
    write_new(target / 'observer.py', read_small(observer_source(), 1024**2))
    os.chmod(target / 'host.py', 0o700)
    tools = target / 'tools'
    tools.mkdir(mode=0o700)
    for name in ('device_lease.py', 'run_hardware_smoke.py'):
        write_new(tools / name, read_small(legacy / 'tools' / name, 1024**2))
    make_payload(target / 'payload.tar.gz', sources)
    verify_payload(target / 'payload.tar.gz', sources)
    plan = {'schema': 1, 'label': label, 'owner': owner, 'origin': 'stock-tap', 'case': CASE,
            'sources': {name: str(path) for name, path in sources.items()},
            'reference': str(reference), 'navigation': str(navigation), 'legacy': str(legacy),
            'device': str(device), 'guard': str(guard), 'approved_guard': str(approved_guard),
            'visit': str(visit), 'dispatch': str(dispatch), 'witness': str(witness)}
    write_new(target / 'plan.json', (json.dumps(plan, indent=2) + '\n').encode())
    write_new(target / 'ready', b'ready\n')
    return label


def checked_plan(work, owner, *, minimal=False):
    plan_dir = private_directory(work / 'plan')
    need(read_small(plan_dir / 'ready', 16) == b'ready\n', 'plan_not_ready')
    plan = unique_json(read_small(plan_dir / 'plan.json', 128 * 1024))
    need(plan.get('schema') == 1 and plan.get('origin') == 'stock-tap' and plan.get('case') == CASE
         and plan.get('owner') == owner_id(owner) and isinstance(plan.get('label'), str)
         and re.fullmatch(r'[0-9a-f]{16}', plan['label']), 'invalid_plan')
    need(read_small(plan_dir / 'host.py') == read_small(HERE / 'host.py'), 'changed_frozen_operator')
    for name in ('device.sh', 'guard.sh', 'rule', 'pilot_pixels.py', 'observer.py',
                 'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
        read_small(plan_dir / name)
    if minimal:
        return plan
    legacy = private_directory(Path(plan['legacy']))
    reference, navigation = Path(plan['reference']), Path(plan['navigation'])
    closed_reference(reference, 'reference', legacy)
    closed_reference(navigation, 'navigation', legacy)
    need(same_file(Path(plan['guard']), Path(plan['approved_guard'])), 'guard_differs_from_approved')
    sources = input_sources(legacy, navigation, *(Path(plan[key]) for key in ('guard', 'visit', 'dispatch', 'witness')))
    need(plan.get('sources') == {name: str(path) for name, path in sources.items()}, 'changed_source_plan')
    verify_payload(plan_dir / 'payload.tar.gz', sources)
    need(read_small(plan_dir / 'device.sh', 128 * 1024) == read_small(Path(plan['device']), 128 * 1024)
         and read_small(plan_dir / 'guard.sh', 128 * 1024) == read_small(Path(plan['guard']), 128 * 1024)
         and read_small(plan_dir / 'rule', 16 * 1024) == read_small(reference / 'rule', 16 * 1024),
         'changed_frozen_input')
    for name in ('host.py', 'pilot_pixels.py'):
        need(read_small(plan_dir / name) == read_small(HERE / name), 'changed_frozen_operator')
    need(read_small(plan_dir / 'observer.py') == read_small(observer_source()), 'changed_frozen_operator')
    for name in ('device_lease.py', 'run_hardware_smoke.py'):
        need(read_small(plan_dir / 'tools' / name) == read_small(legacy / 'tools' / name), 'changed_frozen_tool')
    return plan


def cleanup_gate():
    result = subprocess.run([str(CLEANUP), '--status'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=20, check=False)
    need(result.returncode == 0 and len(result.stdout) <= 8192, 'cleanup_status_unavailable')
    record = unique_json(result.stdout)
    need(all(record.get(key) is True for key in ('resolved_by_admin_cleanup', 'cleanup_only',
             'new_lease_administratively_retired', 'ordinary_acquisition_unblocked_at_cleanup'))
         and record.get('restoration_verified') is False and record.get('original_owner_release') is False,
         'cleanup_status_not_verified')
    return True


def live_lease_gate():
    need(not os.path.lexists(LEASE), 'lease_busy')


def input_secret():
    with open('/dev/tty', 'w', encoding='utf-8') as terminal:
        need(os.isatty(terminal.fileno()), 'private_terminal_required')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass('Kindle password: ', stream=terminal)
    secret = bytearray(value.encode('utf-8'))
    value = None
    if not secret or len(secret) > SECRET_LIMIT or any(value in secret for value in (0, 10, 13)):
        secret[:] = b'\0' * len(secret)
        raise Stop('invalid_password')
    return secret


def secret_pipe(secret):
    need(isinstance(secret, bytearray) and 0 < len(secret) <= SECRET_LIMIT, 'invalid_password')
    reader, writer = os.pipe2(os.O_CLOEXEC)
    try:
        offset = 0
        while offset < len(secret):
            count = os.write(writer, memoryview(secret)[offset:])
            need(count > 0, 'secret_pipe_failed')
            offset += count
    except BaseException:
        os.close(reader)
        raise
    finally:
        os.close(writer)
    return reader


def askpass():
    parent, descriptor = os.environ.get('AIDOKU_PILOT_PARENT', ''), os.environ.get('AIDOKU_PILOT_FD', '')
    if not parent.isdecimal() or not descriptor.isdecimal():
        return 1
    try:
        direct = os.getppid()
        if direct != int(parent):
            fields = Path(f'/proc/{direct}/stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[1]) != int(parent):
                return 1
        fd = os.open(f'/proc/{parent}/fd/{descriptor}', os.O_RDONLY | os.O_CLOEXEC)
        try:
            value = os.read(fd, SECRET_LIMIT + 1)
        finally:
            os.close(fd)
        if not value or len(value) > SECRET_LIMIT or any(char in value for char in (0, 10, 13)):
            return 1
        os.write(1, value + b'\n')
        return 0
    except (OSError, ValueError, IndexError):
        return 1


@contextmanager
def askpass_environment():
    values = {'SSH_ASKPASS': str(Path(__file__).resolve()), 'SSH_ASKPASS_REQUIRE': 'force'}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def blocked_signals():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def work_gate(work):
    descriptor = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def memfile(name, *chunks):
    descriptor = os.memfd_create(name, os.MFD_CLOEXEC)
    try:
        for chunk in chunks:
            remaining = memoryview(chunk)
            while remaining:
                count = os.write(descriptor, remaining)
                need(count > 0, 'transport_input_failed')
                remaining = remaining[count:]
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class Transport:
    def __init__(self, argv, plan_dir, label, secret, *, recovery=False):
        self.argv, self.label, self.secret = argv, label, secret
        self.device = read_small(plan_dir / 'device.sh', 128 * 1024)
        self.guard = read_small(plan_dir / 'guard.sh', 128 * 1024)
        self.last_stats = None
        payload = b'' if recovery else regular(plan_dir / 'payload.tar.gz', MAX_PAYLOAD).read_bytes()
        self.fds = []
        try:
            self.fds.append(memfile('pilot-device', self.device))
            self.fds.append(memfile('pilot-guard', self.guard))
            self.fds.append(memfile('pilot-prepare', self.device, payload))
        except BaseException:
            self.close()
            raise

    def close(self):
        for descriptor in self.fds:
            os.close(descriptor)
        self.fds = []

    def __call__(self, operation):
        started = monotonic()
        stats = {'stdout_bytes': 0, 'stderr_bytes': 0, 'stderr_truncated': False, 'elapsed_ms': 0}
        self.last_stats = stats
        body = self.guard if operation == 'guard' else self.device
        descriptor = self.fds[1] if operation == 'guard' else self.fds[2] if operation == 'prepare' else self.fds[0]
        args = {'guard': ['admit'], 'admit': ['admit'], 'prepare': ['prepare', 'tapcheck'],
                'pilot': ['pilot', '2500', 'require-touch'], 'collect': ['collect-case', CASE],
                'close': ['close'], 'absent': ['absent']}[operation]
        command = f'exec sh -c "$(dd bs=1 count={len(body)} 2>/dev/null)" sh ' + shlex.join(
            [args[0], self.label, *args[1:]])
        secret_fd, child = None, None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            with blocked_signals():
                secret_fd = secret_pipe(self.secret)
            env = os.environ.copy()
            env.update(SSH_ASKPASS=str(Path(__file__).resolve()), SSH_ASKPASS_REQUIRE='force',
                       AIDOKU_PILOT_ASKPASS='1', AIDOKU_PILOT_PARENT=str(os.getpid()),
                       AIDOKU_PILOT_FD=str(secret_fd), LC_ALL='C')
            try:
                with blocked_signals():
                    child = subprocess.Popen([*self.argv, command], stdin=descriptor, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, start_new_session=True, env=env)
            except OSError:
                raise Stop('transport_spawn_error') from None
            outputs = [bytearray(), bytearray()]
            deadline = monotonic() + TIMEOUTS[operation]
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
                while selector.get_map():
                    ready = selector.select(max(0, deadline - monotonic()))
                    need(ready and monotonic() < deadline, 'transport_timeout')
                    for item, _ in ready:
                        if item.data == 'stdout':
                            limit = MAX_EVIDENCE if operation == 'collect' else 4096
                            chunk = os.read(item.fd, min(65536, limit - len(outputs[0]) + 1))
                            stats['stdout_bytes'] += len(chunk)
                            outputs[0].extend(chunk)
                            need(len(outputs[0]) <= limit, 'transport_stdout_limit')
                        else:
                            chunk = os.read(item.fd, 65536)
                            stats['stderr_bytes'] += len(chunk)
                            if len(chunk) >= STDERR_TAIL:
                                outputs[1][:] = chunk[-STDERR_TAIL:]
                            else:
                                excess = len(outputs[1]) + len(chunk) - STDERR_TAIL
                                if excess > 0:
                                    del outputs[1][:excess]
                                outputs[1].extend(chunk)
                            stats['stderr_truncated'] = stats['stderr_bytes'] > STDERR_TAIL
                        if not chunk:
                            selector.unregister(item.fileobj)
            try:
                code = child.wait(timeout=max(0, deadline - monotonic()))
            except subprocess.TimeoutExpired:
                raise Stop('transport_timeout') from None
            return code, bytes(outputs[0]), bytes(outputs[1])
        except (OSError, ValueError):
            raise Stop('transport_io_error') from None
        finally:
            try:
                if secret_fd is not None:
                    os.close(secret_fd)
            finally:
                try:
                    if child is not None:
                        with blocked_signals():
                            try:
                                try:
                                    os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                child.wait(timeout=10)
                            except (Exception, KeyboardInterrupt):
                                raise TransportUnreaped('transport_not_reaped') from None
                            finally:
                                child.stdout.close()
                                child.stderr.close()
                finally:
                    stats['elapsed_ms'] = max(0, int((monotonic() - started) * 1000))


def checked_identity(proof, request):
    need(isinstance(proof, dict) and all(isinstance(proof.get(key), str) and proof[key] for key in IDENTITY)
         and proof['project'] == 'Aidoku' and proof['owner_thread'] == request.owner_thread
         and proof['run_id'] == request.run_id, 'invalid_lease_identity')
    return {key: proof[key] for key in IDENTITY}


def owned(lease, request, identity):
    proof = lease.status(request)
    need(proof.get('ok') is True and proof.get('operation') == 'status' and proof.get('state') == 'held'
         and proof.get('ownership_verified') is True and checked_identity(proof, request) == identity,
         'lease_ownership_lost')


def release_owned(lease, request, identity):
    owned(lease, request, identity)
    with blocked_signals():
        proof = lease.release(request, native_restored_and_connections_closed=True)
    need(isinstance(proof, dict) and proof.get('operation') == 'release' and proof.get('state') == 'released'
         and all(proof.get(key) is True for key in ('ok', 'owner_and_token_checked',
                 'lease_directory_absent_after_release', 'native_restored_and_connections_closed'))
         and checked_identity(proof, request) == identity, 'release_unverified')
    return proof


def failure_detail(status, stderr):
    if not isinstance(stderr, bytes):
        return None
    found = re.findall(rb'^refused ([a-z][a-z0-9_]{0,47})\r?$', stderr, re.MULTILINE)
    for value in reversed(found):
        tag = value.decode('ascii')
        if tag in SAFE_REFUSALS:
            return tag
    if status == 255 and b'Permission denied' in stderr:
        return 'authentication_failed'
    if status == 255:
        for marker, reason in ((b'Connection refused', 'connection_refused'),
                               (b'No route to host', 'no_route'),
                               (b'Network is unreachable', 'network_unreachable'),
                               (b'Connection timed out', 'connection_timeout'),
                               (b'Connection reset', 'connection_reset')):
            if marker in stderr:
                return reason
    return None


def transport_stats(remote):
    value = getattr(remote, 'last_stats', None)
    if not isinstance(value, dict):
        return None
    keys = ('stdout_bytes', 'stderr_bytes', 'elapsed_ms')
    if (any(type(value.get(key)) is not int or not 0 <= value[key] <= 10**12 for key in keys)
            or type(value.get('stderr_truncated')) is not bool):
        return None
    return {key: value[key] for key in (*keys, 'stderr_truncated')}


def transport_reason(error):
    if isinstance(error, PilotInterrupted):
        return error.reason
    if isinstance(error, KeyboardInterrupt):
        return 'interrupted'
    if isinstance(error, TimeoutError):
        return 'transport_timeout'
    if isinstance(error, OSError):
        return 'transport_io_error'
    if isinstance(error, Stop) and str(error) in TRANSPORT_ERRORS:
        return str(error)
    return 'transport_error'


def operation_result(operation, good, status, stderr, remote=None):
    result = {'operation': operation, 'outcome': 'ok' if good else 'failed'}
    if type(status) is int and -255 <= status <= 255:
        result['exit_code'] = status
    detail = failure_detail(status, stderr) if not good else None
    if detail is not None:
        result['refusal'] = detail
    stats = transport_stats(remote)
    if stats is not None:
        result['transport'] = stats
    return result


def unknown_operation(operation, error, remote):
    result = {'operation': operation, 'outcome': 'unknown', 'transport_error': transport_reason(error)}
    stats = transport_stats(remote)
    if stats is not None:
        result['transport'] = stats
    return result


def operate(*, lease, request, remote, save, save_archive):
    report = {'schema': 1, 'request': {'owner_thread': request.owner_thread, 'run_id': request.run_id},
              'operations': [], 'phase': 'acquire', 'identity': None, 'released': False,
              'recovery_required': True, 'operations_passed': False, 'mutation_possible': False}
    held = False
    mutation = False
    close_confirmed = False
    durable = True
    transport_reaped = True

    def mark():
        nonlocal durable
        try:
            save(report)
        except (Exception, KeyboardInterrupt):
            durable = False
            raise Stop('checkpoint_failed') from None

    def call(operation):
        nonlocal transport_reaped
        owned(lease, request, report['identity'])
        report['phase'] = operation
        mark()
        try:
            status, stdout, stderr = remote(operation)
        except TransportUnreaped:
            transport_reaped = False
            report['transport_unreaped'] = True
            report['operations'].append({'operation': operation, 'outcome': 'transport_not_reaped'})
            raise Stop('transport_not_reaped') from None
        except (Exception, KeyboardInterrupt) as error:
            report['operations'].append(unknown_operation(operation, error, remote))
            raise Stop(operation + '_' + transport_reason(error)) from None
        good = type(status) is int and status == 0 and isinstance(stdout, bytes) and isinstance(stderr, bytes)
        if operation == 'collect':
            good = good and 10 <= len(stdout) <= MAX_EVIDENCE and stdout.startswith(b'\x1f\x8b\x08')
        else:
            good = good and stdout == REPLIES[operation]
        report['operations'].append(operation_result(operation, good, status, stderr, remote))
        mark()
        need(good, operation + '_failed')
        return stdout

    try:
        mark()
        try:
            with blocked_signals():
                proof = lease.acquire(request)
                held = True
        except (Exception, KeyboardInterrupt) as error:
            details = getattr(error, 'details', None)
            untouched = ('lease_directory_created', 'private_token_created', 'owner_record_created')
            if (isinstance(getattr(lease, 'LeaseError', None), type)
                    and isinstance(error, lease.LeaseError)
                    and getattr(error, 'code', None) in ('lease_exists', 'operation_busy')
                    and isinstance(details, dict) and all(details.get(key) is False for key in untouched)):
                report['error'], report['recovery_required'] = 'lease_busy', False
            else:
                report['error'] = 'acquisition_unknown'
            mark()
            return report
        need(proof.get('ok') is True and proof.get('operation') == 'acquire' and proof.get('state') == 'held',
             'acquisition_unverified')
        report['identity'] = checked_identity(proof, request)
        report['acquisition'] = proof
        mark()
        call('guard')
        call('admit')
        mutation = True
        report['mutation_possible'] = True
        call('prepare')
        call('pilot')
        evidence = call('collect')
        save_archive(evidence)
        report['evidence_saved'] = True
        mark()
    except (Exception, KeyboardInterrupt) as error:
        report['error'] = str(error) if isinstance(error, Stop) else 'operation_interrupted'
    finally:
        if held and report['identity'] is not None:
            if mutation and transport_reaped:
                pending_remote = None
                try:
                    owned(lease, request, report['identity'])
                    report['phase'] = 'close'
                    try:
                        mark()
                    except Stop:
                        pass
                    pending_remote = 'close'
                    status, stdout, stderr = remote('close')
                    pending_remote = None
                    close_confirmed = (type(status) is int and status == 0 and stdout == REPLIES['close']
                                       and isinstance(stderr, bytes))
                    report['operations'].append(operation_result('close', close_confirmed, status, stderr, remote))
                    if not close_confirmed:
                        report['close_error'] = 'close_failed'
                    if close_confirmed:
                        report['close_confirmed'] = True
                        close_confirmed = False
                        try:
                            mark()
                        except Stop:
                            pass
                        owned(lease, request, report['identity'])
                        report['phase'] = 'absent'
                        try:
                            mark()
                        except Stop:
                            pass
                        pending_remote = 'absent'
                        status, stdout, stderr = remote('absent')
                        pending_remote = None
                        absent = (type(status) is int and status == 0 and stdout == REPLIES['absent']
                                  and isinstance(stderr, bytes))
                        report['operations'].append(operation_result('absent', absent, status, stderr, remote))
                        if not absent:
                            report['close_error'] = 'absence_failed'
                        close_confirmed = absent
                        if absent:
                            report['absence_confirmed'] = True
                            try:
                                mark()
                            except Stop:
                                pass
                except TransportUnreaped:
                    transport_reaped = False
                    report['transport_unreaped'] = True
                    report['close_error'] = 'transport_not_reaped'
                    if pending_remote is not None:
                        report['operations'].append({'operation': pending_remote, 'outcome': 'transport_not_reaped'})
                except (Exception, KeyboardInterrupt) as error:
                    if pending_remote is not None:
                        report['operations'].append(unknown_operation(pending_remote, error, remote))
                        report['close_error'] = pending_remote + '_' + transport_reason(error)
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
                    report['release'] = release_owned(lease, request, report['identity'])
                    report['released'], report['recovery_required'] = True, False
                    report['operations_passed'] = bool(mutation and report.get('evidence_saved')
                                                       and 'error' not in report)
                except (Exception, KeyboardInterrupt):
                    report['release_error'] = 'release_unverified'
        try:
            mark()
        except Stop:
            report['error'] = 'checkpoint_failed'
    return report


def recover(*, lease, request, remote, report, save):
    expected = {'owner_thread': request.owner_thread, 'run_id': request.run_id}
    identity = report.get('identity')
    operations = report.get('operations')
    need(report.get('schema') == 1 and report.get('request') == expected
         and report.get('released') is False and report.get('recovery_required') is True
         and type(report.get('mutation_possible')) is bool and isinstance(operations, list)
         and isinstance(identity, dict) and checked_identity(identity, request) == identity
         and report.get('transport_unreaped') is not True, 'manual_reconciliation_required')
    for flag, operation in (('close_confirmed', 'close'), ('absence_confirmed', 'absent')):
        if report.get(flag) is True:
            need(any(item.get('operation') == operation and item.get('outcome') == 'ok'
                     for item in operations if isinstance(item, dict)), 'invalid_recovery_checkpoint')
    need(report.get('absence_confirmed') is not True or report.get('close_confirmed') is True,
         'invalid_recovery_checkpoint')

    def mark():
        try:
            save(report)
        except (Exception, KeyboardInterrupt):
            raise Stop('checkpoint_failed') from None

    def call(operation):
        owned(lease, request, identity)
        report['phase'] = 'recovery_' + operation
        mark()
        try:
            status, stdout, stderr = remote(operation)
        except TransportUnreaped:
            report['transport_unreaped'] = True
            raise Stop('transport_not_reaped') from None
        except (Exception, KeyboardInterrupt) as error:
            operations.append(unknown_operation(operation, error, remote))
            raise Stop(operation + '_' + transport_reason(error)) from None
        good = type(status) is int and status == 0 and stdout == REPLIES[operation] and isinstance(stderr, bytes)
        operations.append(operation_result(operation, good, status, stderr, remote))
        need(good, operation + '_failed')
        report['close_confirmed' if operation == 'close' else 'absence_confirmed'] = True
        mark()

    report['operations_passed'] = False
    try:
        owned(lease, request, identity)
        if report['mutation_possible']:
            if report.get('close_confirmed') is not True:
                call('close')
            if report.get('absence_confirmed') is not True:
                call('absent')
        report['phase'] = 'recovery_release'
        mark()
        report['release'] = release_owned(lease, request, identity)
        report['released'], report['recovery_required'] = True, False
        report.pop('close_error', None)
        report.pop('release_error', None)
        mark()
    except (Exception, KeyboardInterrupt) as error:
        report['recovery_error'] = str(error) if isinstance(error, Stop) else 'recovery_interrupted'
        try:
            mark()
        except Stop:
            pass
    return report


def archive_members(data):
    base = 'out/' + CASE + '/'
    required = {'origin', 'anchor', 'pointer-brackets', 'qualified', 'stock.frame', 'trace', 'touch'}
    optional = {'control.before.frame', 'control.after.frame', 'control.anchor', 'control.method'}
    seen = {}
    need(10 <= len(data) <= MAX_EVIDENCE, 'invalid_evidence_size')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r|gz') as archive:
        for item in archive:
            need(item.name.startswith(base), 'unexpected_evidence_member')
            name = item.name[len(base):]
            need(name in required | optional and name not in seen and item.isreg() and not item.sparse
                 and 0 < item.size <= 5_000_000, 'unexpected_evidence_member')
            stream = archive.extractfile(item)
            content = stream.read(item.size + 1)
            need(len(content) == item.size, 'truncated_evidence_member')
            seen[name] = content
    need(required <= seen.keys(), 'missing_evidence_member')
    need(seen['origin'] == b'stock-tap\n' and seen['qualified'] == (CASE + ' stock-tap\n').encode(),
         'unbound_evidence')
    need(seen['anchor'] == seen['pointer-brackets'], 'pointer_brackets_differ')
    match = re.fullmatch(rb'([1-9][0-9]{0,19}) ([1-9][0-9]{0,19}) '
                         rb'([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})\n', seen['anchor'])
    need(match is not None, 'invalid_release_brackets')
    brackets = tuple(map(int, match.groups()))
    need(brackets[0] <= brackets[1] < brackets[2] <= brackets[3], 'invalid_release_brackets')
    if 'control.method' in seen:
        need(seen['control.method'] == b'manual-window\n', 'unexpected_control_method')
    return seen, brackets


def verified_report(work, plan):
    attempt = private_directory(work / 'attempt')
    report = unique_json(read_small(attempt / 'operations.json', 256 * 1024))
    request = {'owner_thread': plan['owner'], 'run_id': 'aidoku-stockpilot-' + plan['label']}
    release, identity = report.get('release'), report.get('identity')
    expected = ['guard', 'admit', 'prepare', 'pilot', 'collect', 'close', 'absent']
    operations = report.get('operations')
    need(report.get('schema') == 1 and report.get('request') == request
         and report.get('operations_passed') is True and report.get('released') is True
         and report.get('recovery_required') is False and report.get('close_confirmed') is True
         and report.get('absence_confirmed') is True and report.get('evidence_saved') is True
         and isinstance(operations, list) and [item.get('operation') for item in operations] == expected
         and all(item.get('outcome') == 'ok' for item in operations)
         and isinstance(identity, dict) and isinstance(release, dict)
         and all(isinstance(identity.get(key), str) and identity[key] and release.get(key) == identity[key]
                 for key in IDENTITY)
         and identity.get('project') == 'Aidoku' and identity.get('owner_thread') == request['owner_thread']
         and identity.get('run_id') == request['run_id'] and release.get('ok') is True
         and release.get('operation') == 'release' and release.get('state') == 'released'
         and all(release.get(key) is True for key in ('owner_and_token_checked',
                 'lease_directory_absent_after_release', 'native_restored_and_connections_closed')),
         'unverified_visit_or_release')
    return attempt


def analyze(work, plan):
    attempt = verified_report(work, plan)
    try:
        evidence = regular(attempt / 'evidence.tar.gz', MAX_EVIDENCE).read_bytes()
        members, brackets = archive_members(evidence)
        pixels = load_module('stock_pilot_pixels', work / 'plan/pilot_pixels.py')
        with tempfile.TemporaryDirectory(prefix='analysis-', dir=attempt) as directory:
            root = Path(directory)
            stock, trace = root / 'stock.frame', root / 'trace'
            write_new(stock, members['stock.frame'])
            write_new(trace, members['trace'])
            result = pixels.analyze_visit(work / 'plan/rule', trace, stock, brackets, members['touch'])
        need(isinstance(result, dict) and result.get('physical_sort_tap_observed') is True
             and result.get('first_response_is_upper_bound') is True
             and type(result.get('ready_upper_ms')) in (int, float), 'invalid_measurement_result')
        output = {'ok': True, 'measurement': result, 'released': True,
                  'cleanup_only_not_prior_restoration': True}
    except (Stop, ValueError, OSError, tarfile.TarError) as error:
        reason = str(error)
        if not re.fullmatch(r'[a-z0-9_]{1,80}', reason):
            reason = 'invalid_or_incomplete_evidence'
        output = {'ok': False, 'measurement': 'not_observed', 'reason': reason, 'released': True}
    data = (json.dumps(output, indent=2, allow_nan=False) + '\n').encode()
    result_path = attempt / 'measurement.json'
    if os.path.lexists(result_path):
        need(read_small(result_path, 64 * 1024) == data, 'measurement_already_recorded')
    else:
        write_new(result_path, data)
    print(json.dumps(output, allow_nan=False))
    return 0 if output['ok'] else 2


def status_report(work):
    attempt = work / 'attempt'
    if not os.path.lexists(attempt):
        return {'prepared': True, 'attempt': 'not_started', 'measurement_recorded': False}
    private_directory(attempt)
    if not os.path.lexists(attempt / 'operations.json'):
        return {'prepared': True, 'attempt': 'unknown', 'recovery_required': True,
                'measurement_recorded': False}
    report = unique_json(read_small(attempt / 'operations.json', 256 * 1024))
    return {'prepared': True, 'attempt': 'recorded', 'phase': report.get('phase'),
            'released': report.get('released') is True, 'recovery_required': report.get('recovery_required') is True,
            'operations_passed': report.get('operations_passed') is True,
            'measurement_recorded': os.path.lexists(attempt / 'measurement.json')}


def interrupted(signum, _frame):
    raise PilotInterrupted(signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--freeze', action='store_true')
    actions.add_argument('--preflight', action='store_true')
    actions.add_argument('--run', action='store_true')
    actions.add_argument('--recover', action='store_true')
    actions.add_argument('--analyze', action='store_true')
    actions.add_argument('--status', action='store_true')
    parser.add_argument('work', type=Path)
    parser.add_argument('--owner', required=True)
    for name in ('legacy', 'reference', 'navigation', 'device', 'guard', 'approved-guard', 'visit', 'dispatch', 'witness'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    need(sys.platform == 'linux' and WORKER.is_file(), 'test_env_host_required')
    work = private_directory(args.work)
    owner = owner_id(args.owner)
    if args.freeze:
        need(all(getattr(args, name) is not None for name in ('legacy', 'reference', 'navigation', 'device', 'guard',
                 'approved_guard', 'visit', 'dispatch', 'witness')), 'freeze_inputs_required')
        with work_gate(work):
            label = freeze(work, args.legacy, args.reference, args.navigation, owner, args.device, args.guard,
                           args.approved_guard, args.visit, args.dispatch, args.witness)
        print(json.dumps({'frozen': True, 'label': label, 'lease_requested': False}))
        return 0
    plan = checked_plan(work, owner, minimal=args.recover or args.analyze or args.status)
    if args.status:
        print(json.dumps(status_report(work)))
        return 0
    if args.analyze:
        return analyze(work, plan)
    if not args.recover:
        need(not os.path.lexists(work / 'attempt'), 'plan_already_used')
    lease = load_module('pilot_lease', work / 'plan/tools/device_lease.py')
    ssh = load_module('pilot_ssh', work / 'plan/tools/run_hardware_smoke.py')
    lease.PROJECT = 'Aidoku'
    need(os.access(Path(__file__).resolve(), os.X_OK), 'host_script_must_be_executable')
    with askpass_environment():
        ssh_argv = ssh.ssh_argv(AUTH / 'ssh_config', 'kindle-physical', AUTH / 'known_hosts')
    if args.preflight:
        cleanup_gate()
        try:
            live_lease_gate()
            lease_free = True
        except Stop as error:
            if str(error) != 'lease_busy':
                raise
            lease_free = False
        print(json.dumps({'ready': lease_free, 'package_verified': True, 'ssh_pin_configured_offline': True,
                          'cleanup_only_status_verified': True, 'restoration_verified': False,
                          'original_owner_release': False, 'live_lease_free': lease_free, 'unused_plan': True,
                          'lease_requested': False}))
        return 0 if lease_free else 3
    with work_gate(work):
        attempt = work / 'attempt'
        if args.recover:
            private_directory(attempt)
            report = unique_json(read_small(attempt / 'operations.json', 256 * 1024))
            need(report.get('schema') == 1 and report.get('released') is False
                 and report.get('recovery_required') is True and report.get('transport_unreaped') is not True
                 and report.get('request') == {'owner_thread': owner, 'run_id': 'aidoku-stockpilot-' + plan['label']}
                 and isinstance(report.get('identity'), dict), 'manual_reconciliation_required')
        else:
            need(not os.path.lexists(attempt), 'plan_already_used')
            print('Be ready at the device before entering the password; tap the Library sort control once when visible.',
                  flush=True)
        secret = input_secret()
        remote = None
        try:
            remote = Transport(ssh_argv, work / 'plan', plan['label'], secret, recovery=args.recover)
            request = lease.LeaseRequest(LEASE, attempt / 'token', owner, 'aidoku-stockpilot-' + plan['label'])
            if args.recover:
                report = recover(lease=lease, request=request, remote=remote, report=report,
                                 save=lambda value: checkpoint(attempt / 'operations.json', value))
            else:
                cleanup_gate()
                live_lease_gate()
                attempt.mkdir(mode=0o700)
                report = operate(lease=lease, request=request, remote=remote,
                                 save=lambda value: checkpoint(attempt / 'operations.json', value),
                                 save_archive=lambda value: write_new(attempt / 'evidence.tar.gz', value))
        finally:
            try:
                if remote is not None:
                    remote.close()
            finally:
                secret[:] = b'\0' * len(secret)
    if args.recover:
        print(json.dumps({'released': report.get('released'), 'recovery_required': report.get('recovery_required'),
                          'error': report.get('recovery_error')}))
        return 0 if report.get('released') is True else 1
    if report.get('operations_passed') is not True:
        refusals = [item.get('refusal') for item in report.get('operations', []) if item.get('refusal')]
        print(json.dumps({'operations_passed': False, 'released': report.get('released'),
                          'recovery_required': report.get('recovery_required'),
                          'error': report.get('error'), 'close_error': report.get('close_error'),
                          'release_error': report.get('release_error'),
                          'refusal': refusals[-1] if refusals else None}))
        return 1
    return analyze(work, plan)


if __name__ == '__main__':
    if os.environ.get('AIDOKU_PILOT_ASKPASS') == '1':
        raise SystemExit(askpass())
    for signum in SIGNALS:
        signal.signal(signum, interrupted)
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, Stop) else 'operation_failed'}),
              file=sys.stderr)
        raise SystemExit(1)
