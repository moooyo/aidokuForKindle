"""Prepare or operate one isolated launcher visit on the designated worker."""

import argparse
from contextlib import contextmanager
import fcntl
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
from time import monotonic

from evidence import analyze_archive
from lifecycle import IDENTITY, operate
from payload import load, rewrite_launcher


HERE = Path(__file__).resolve().parent
PRIOR = Path('/var/tmp/kindle-build-coordination/aidoku-operator-recovery-20261121.json')
ADMIN_CLOSURE = Path('/var/tmp/kindle-build-coordination/aidoku-admin-closure-20261121.json')
ADMIN_ROOT = Path('/var/tmp')
LEASE = Path('/tmp/codex-kindle-physical-test.lock')
AUTH = Path('/root/.config/kindle-physical')
TIMEOUT = {'admit': 18, 'prepare': 210, 'visit': 60, 'collect': 30, 'restore': 100, 'close': 80, 'absent': 10}
WORKER_RUNNER = Path('/opt/kindle-toolchain/ubuntu-run')


def small_file(path, limit=1024**2):
    if path.resolve(strict=True) != path or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f'Unexpected file: {path.name}')
    return path.read_bytes()


def admin_directory(path):
    info = path.lstat()
    if (not path.is_absolute() or path.resolve(strict=True) != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('Invalid administrative evidence directory')


def admin_record(path):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('Invalid administrative evidence path')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 8192):
            raise ValueError('Invalid administrative evidence file')
        chunks = []
        remaining = 8193
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        after = os.fstat(descriptor)
        linked = path.lstat()
        identity = lambda entry: (entry.st_dev, entry.st_ino, entry.st_uid, entry.st_mode, entry.st_nlink,
                                  entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after) or identity(after) != identity(linked):
            raise ValueError('Administrative evidence changed')
    finally:
        os.close(descriptor)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate administrative evidence field')
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('Invalid administrative evidence')
    return value


def admin_returned(original):
    try:
        receipt = admin_record(ADMIN_CLOSURE)
        workspace_name = receipt.get('admin_workspace')
        attempt_name = receipt.get('attempt')
        if (not isinstance(workspace_name, str)
                or re.fullmatch(r'aidoku-admin-reconcile-20261121-[A-Za-z0-9_-]{1,64}', workspace_name) is None
                or not isinstance(attempt_name, str)
                or re.fullmatch(r'repair-run-[a-z0-9_]{8}', attempt_name) is None):
            return False
        workspace = ADMIN_ROOT / workspace_name
        attempt = workspace / attempt_name
        admin_directory(workspace)
        admin_directory(attempt)
        intent = admin_record(workspace / 'intent.json')
        retired = admin_record(workspace / 'retired.json')
        native = admin_record(attempt / 'native-closed.json')
        release = admin_record(attempt / 'release.json')
        old = intent.get('old')
        acquisition = native.get('acquisition')
        if not isinstance(old, dict) or not isinstance(acquisition, dict):
            return False
        old_fields = ('project', 'owner_thread', 'run_id', 'acquired_at_utc')
        release_flags = ('ok', 'native_restored_and_connections_closed', 'owner_and_token_checked',
                         'only_owned_owner_json_removed_then_rmdir', 'lease_directory_absent_after_release')
        if (receipt.get('kind') != 'administrative_native_closure'
                or any(receipt.get(key) is not True for key in ('native_state_verified',
                       'maintenance_lease_released', 'ordinary_acquisition_unblocked'))
                or receipt.get('original_owner_release') is not False
                or intent.get('action') != 'retire_orphan_lease_host_only'
                or intent.get('authorization') != 'User confirmed that the previous operator ended and directed stale lease cleanup.'
                or old.get('state') != 'held'
                or any(not isinstance(original.get(key), str) or not original[key]
                       or old.get(key) != original[key] for key in old_fields)
                or retired.get('kind') != 'administrator_orphan_retirement'
                or retired.get('old_active_lease_removed') is not True
                or retired.get('old_record_quarantined_intact') is not True
                or retired.get('normal_release') is not False
                or retired.get('previous_project') != original['project']
                or retired.get('previous_owner_thread') != original['owner_thread']
                or retired.get('previous_run_id') != original['run_id']
                or not isinstance(intent.get('authorized_operator'), str)
                or not intent['authorized_operator']
                or retired.get('administrator') != intent['authorized_operator']
                or intent['authorized_operator'] == original['owner_thread']
                or receipt.get('previous_run_id') != original['run_id']
                or native.get('kind') != 'admin_repair_confirmed' or native.get('verified') is not True
                or native.get('previous_run_id') != original['run_id'] or native.get('attempt') != attempt_name
                or acquisition.get('ok') is not True or acquisition.get('operation') != 'acquire'
                or acquisition.get('state') != 'held' or acquisition.get('project') != original['project']
                or any(not isinstance(acquisition.get(key), str) or not acquisition[key] for key in IDENTITY)
                or acquisition['owner_thread'] == original['owner_thread']
                or acquisition['run_id'] == original['run_id']
                or not isinstance(original.get('lease_token_sha256'), str)
                or re.fullmatch(r'[0-9a-f]{64}', original['lease_token_sha256']) is None
                or re.fullmatch(r'[0-9a-f]{64}', acquisition['lease_token_sha256']) is None
                or acquisition['lease_token_sha256'] == original['lease_token_sha256']
                or receipt.get('maintenance_run_id') != acquisition['run_id']
                or release.get('operation') != 'release' or release.get('state') != 'released'
                or any(release.get(key) is not True for key in release_flags)
                or any(release.get(key) != acquisition[key] for key in IDENTITY)):
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return False


def prior_returned(index=PRIOR):
    batch = Path(json.loads(small_file(index))['batch'])
    if not batch.is_absolute() or batch.resolve(strict=True) != batch:
        raise ValueError('Invalid predecessor batch')
    original = json.loads(small_file(batch / 'receipt.json'))['acquisition']
    if (original.get('ok') is not True or (original.get('project'), original.get('operation'), original.get('state')) != (
            'Aidoku', 'acquire', 'held') or not all(isinstance(original.get(key), str) and original[key] for key in IDENTITY)):
        raise ValueError('Invalid predecessor acquisition')
    for path in batch.glob('recovery-*.json'):
        record = json.loads(small_file(path))
        release = record.get('release') or {}
        operations = record.get('operations') or []
        if (len(operations) == 1 and operations[0].get('operation') == 'close'
                and type(operations[0].get('status')) is int and operations[0]['status'] == 0
                and operations[0].get('observed') == ['device_closed']
                and release.get('operation') == 'release' and release.get('state') == 'released'
                and all(release.get(key) is True for key in ('ok', 'native_restored_and_connections_closed',
                                                              'owner_and_token_checked', 'lease_directory_absent_after_release'))
                and all(release.get(key) == original[key] for key in IDENTITY)):
            return True
    return admin_returned(original)


def private_directory(path):
    info = path.stat()
    if path.resolve(strict=True) != path or not path.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('Use the private worker workspace')


def checkpoint(path, report):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write((json.dumps(report, indent=2, allow_nan=False) + '\n').encode())
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


def check_operator(work, owner, *, seal=False):
    contract = work / 'sealed-operator'
    files = {path.name: path for path in (HERE / 'run_visit.py', HERE / 'device.sh', HERE / 'lifecycle.py',
             HERE / 'evidence.py', HERE / 'payload.py', HERE.parent / 'launcher_stages.py',
             work / 'tools/device_lease.py', work / 'tools/run_hardware_smoke.py')}
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', owner):
        raise ValueError('Use the actual designated Codex operator thread ID')
    key = small_file(work / 'label', 32).decode('ascii').strip()
    if not re.fullmatch('[0-9a-f]{16}', key):
        raise ValueError('Invalid new plan label')
    if seal:
        for target, source in approved_tool_sources(work).items():
            ensure_same_file(target, source, 'Prepared host tools differ from approved sources')
    expected = {name: small_file(path) for name, path in files.items()}
    expected.update(owner=(owner + '\n').encode(), label=(key + '\n').encode())
    if seal:
        contract.mkdir(mode=0o700)
        for name, contents in expected.items():
            with (contract / name).open('xb') as output:
                output.write(contents)
    private_directory(contract)
    if (not seal and small_file(contract / 'ready') != b'') or any(small_file(contract / name) != contents for name, contents in expected.items()):
        raise ValueError('The operator, label or scripts differ from their frozen copies')
    return key, expected['device.sh']


def same_bytes(source, other):
    while True:
        part = source.read(256 * 1024)
        if part != other.read(256 * 1024):
            return False
        if not part:
            return True


def ensure_same_file(target, source, reason):
    if target.resolve(strict=True) != target or not target.is_file():
        raise ValueError(reason)
    if source.resolve(strict=True) != source or not source.is_file():
        raise ValueError(reason)
    with target.open('rb') as left, source.open('rb') as right:
        if not same_bytes(left, right):
            raise ValueError(reason)


def approved_tool_sources(work):
    approved = work / 'review' / 'approved' / 'tools'
    return {
        work / 'tools/device_lease.py': approved / 'device_lease.py',
        work / 'tools/run_hardware_smoke.py': approved / 'run_hardware_smoke.py',
    }


def approved_payload_sources(work):
    approved = work / 'review' / 'approved'
    return {
        'launcher.sh': approved / 'launcher.sh',
        'guard.sh': approved / 'guard.sh',
        'app/aidoku': approved / 'app' / 'aidoku',
        'app/aidoku-startup-portrait': approved / 'app' / 'aidoku-startup-portrait',
    }


def seal_package(work, plan, payload, owner):
    label = small_file(work / 'label', 32).decode('ascii').strip()
    if plan['label'] != label or not re.fullmatch('[0-9a-f]{16}', label):
        raise ValueError('Payload label differs from the planned stage')
    seed = work / 'work/mock-mnt/us' / ('.aidoku-native-' + label) / 'seed'
    inputs = work / 'inputs'
    approved_payload = approved_payload_sources(work)
    for member, source in approved_payload.items():
        ensure_same_file(inputs / Path(member), source, 'Prepared launcher inputs differ from approved sources')
    transformed = rewrite_launcher(small_file(approved_payload['launcher.sh']))
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
        for item in archive:
            content = archive.extractfile(item)
            if content is None:
                raise ValueError('Payload archive entry is unreadable')
            if item.name == 'launch.sh':
                matched = same_bytes(content, io.BytesIO(transformed))
            else:
                if item.name.startswith('seed/'):
                    source = seed / item.name.removeprefix('seed/')
                else:
                    source = approved_payload.get(item.name)
                    if source is None:
                        raise ValueError('Payload member is not approved for sealing')
                if source.resolve(strict=True) != source or not source.is_file():
                    raise ValueError('Expected the prepared regular input')
                with source.open('rb') as original:
                    matched = same_bytes(content, original)
            if not matched:
                raise ValueError('Payload differs from the prepared source, guard, seed or program')
    check_operator(work, owner, seal=True)
    contract = work / 'sealed-operator'
    (contract / 'plan.json').write_bytes(small_file(work / 'plan/plan.json'))
    (contract / 'payload.tar.gz').write_bytes(payload)
    (contract / 'ready').touch(mode=0o600, exist_ok=False)


@contextmanager
def operator_gate(work):
    descriptor = os.open(work, os.O_DIRECTORY | os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def exchange(argv, incoming, seconds, stdout_limit):
    incoming.seek(0)
    deadline = monotonic() + seconds
    outputs = [bytearray(), bytearray()]
    with subprocess.Popen(argv, stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as child:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, (outputs[0], stdout_limit))
                selector.register(child.stderr, selectors.EVENT_READ, (outputs[1], 64 * 1024))
                while selector.get_map():
                    ready = selector.select(max(0, deadline - monotonic()))
                    if not ready or monotonic() >= deadline:
                        raise TimeoutError('Device operation timed out')
                    for item, _ in ready:
                        destination, limit = item.data
                        chunk = os.read(item.fd, min(65536, limit - len(destination) + 1))
                        destination.extend(chunk)
                        if len(destination) > limit:
                            raise ValueError('Oversized device response')
                        if not chunk:
                            selector.unregister(item.fileobj)
            code = child.wait(timeout=max(0, deadline - monotonic()))
            return code, bytes(outputs[0]), bytes(outputs[1])
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise


@contextmanager
def channel(work, ssh, label, body, payload):
    if not 0 < len(body) < 32768 or b'\x00' in body:
        raise ValueError('Invalid device operator script')
    with tempfile.TemporaryFile(dir=work) as script, tempfile.TemporaryFile(dir=work) as setup:
        script.write(body)
        setup.write(body)
        setup.write(payload)

        def remote(operation):
            timeout = TIMEOUT[operation]
            command = f'exec sh -c "$(dd bs=1 count={len(body)} 2>/dev/null)" visit {operation} {label}'
            return exchange([*ssh, command], setup if operation == 'prepare' else script, timeout,
                            8 * 1024**2 if operation == 'collect' else 4096)

        yield remote


def main(args):
    work = args.work.absolute()
    private_directory(work)
    if sys.platform != 'linux' or not WORKER_RUNNER.is_file():
        raise ValueError('Use the test-env host, not a local workstation')
    attempt = work / 'attempt'
    receipt = attempt / 'operations.json'
    archive_path = attempt / 'evidence.tar.gz'
    owner = os.environ.get('CODEX_THREAD_ID', '')
    plan_dir = work / 'plan'
    if args.action == 'analyze':
        private_directory(attempt)
        if json.loads(small_file(receipt)).get('operations_passed') is not True:
            raise ValueError('Only a successful visit with verified release can be analyzed')
        print(json.dumps(analyze_archive(small_file(archive_path, 8 * 1024**2)), indent=2))
        return 0
    if args.action == 'seal':
        plan, payload = load(plan_dir)
        seal_package(work, plan, payload, owner)
        print('Operator and single-use package frozen; no lease requested')
        return 0
    label, body = check_operator(work, owner)
    sys.path.insert(0, str(work / 'tools'))
    import device_lease as lease
    from run_hardware_smoke import ssh_argv
    lease.PROJECT = 'Aidoku'
    ssh = ssh_argv(AUTH / 'ssh_config', 'kindle-physical', AUTH / 'known_hosts')
    recovering = args.action == 'recover'
    if recovering:
        private_directory(attempt)
        payload = b''
    else:
        plan, payload = load(plan_dir)
        contract = work / 'sealed-operator'
        with (contract / 'payload.tar.gz').open('rb') as frozen:
            if (plan['label'] != label or small_file(plan_dir / 'plan.json') != small_file(contract / 'plan.json')
                    or not same_bytes(frozen, io.BytesIO(payload))):
                raise ValueError('Package differs from the source-verified frozen stage')
        gates = {'package_verified': True, 'ssh_pin_configured_offline': True, 'prior_close_verified': prior_returned(),
                 'lease_free': not os.path.lexists(LEASE), 'unused_plan': not os.path.lexists(attempt)}
        if args.action == 'preflight' or not all(gates.values()):
            print(json.dumps(gates))
            return 0 if all(gates.values()) else 3
    with channel(work, ssh, label, body, payload) as remote, operator_gate(work):
        if recovering:
            report = json.loads(small_file(receipt))
            if report.get('request') != {'owner_thread': owner, 'run_id': 'aidoku-launchdiag-' + label}:
                raise ValueError('Recovery requires this plan and its original operator')
            if report.get('state', {}).get('released') or report.get('recovery_required') is not True:
                raise ValueError('There is no outstanding owned recovery for this plan')
        else:
            attempt.mkdir(mode=0o700)
            report = {}
        request = lease.LeaseRequest(LEASE, attempt / 'token', owner, 'aidoku-launchdiag-' + label)
        result = operate(lease=lease, request=request, remote=remote, report=report,
                         checkpoint=lambda value: checkpoint(receipt, value), recover=recovering)
    if result is not None:
        with archive_path.open('xb') as output:
            output.write(result)
    released = report.get('state', {}).get('released') is True
    print(json.dumps({'operations_passed': report.get('operations_passed') is True, 'released': released,
                      'recovery_required': report.get('recovery_required'), 'error': report.get('error')}))
    return 0 if (released if recovering else report.get('operations_passed') is True) else 1


def interrupted(*_):
    raise KeyboardInterrupt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seal', 'preflight', 'run', 'recover', 'analyze'))
    parser.add_argument('work', type=Path)
    os.umask(0o077)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    raise SystemExit(main(parser.parse_args()))
