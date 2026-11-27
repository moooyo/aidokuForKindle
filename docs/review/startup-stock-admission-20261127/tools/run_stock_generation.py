#!/usr/bin/env python3
"""Operate one separately approved Scribe locator or ordinary-card visit."""

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import time


HERE = Path(__file__).resolve().parent
_private = runpy.run_path(str(HERE/'stock_private_inputs.py'))
private_path, device_hostname = (_private[k] for k in ('private_path', 'device_hostname'))
SOURCE = 'f3e5070577e74a29552f772491edac660ec62a53'
DIAGNOSTIC = 'launcher-generation-v1'
OPERATOR = 'stock-admission-v2'
KINDS = ('locator', 'measure')
COMMON_TOOLS = ('flock', 'usleep', 'cmp', 'stat', 'readlink', 'gzip', 'base64', 'sqlite3')
MEASURE_TOOLS = ('mount', 'umount', 'timeout')
COMMON_ADMISSION_REASONS = frozenset({
    'privilege_unverified', 'paths_unverified', 'attempt_paths_not_empty', 'mount_state_unverified',
    'hardware_unverified', 'reader_state_unverified', 'power_not_active_or_unreadable', 'native_foreground_unverified',
    'statusbar_unverified', 'window_manager_unverified', 'profile_unverified', 'space_read_failed',
    'space_unverified', 'space_requirement_not_met',
}) | {'missing_tool_'+tool for tool in COMMON_TOOLS}
MEASURE_ADMISSION_REASONS = frozenset({'timeout_probe_target_missing', 'timeout_selftest_failed'}) | {
    'missing_tool_'+tool for tool in MEASURE_TOOLS
}
ADMIT_BOOT_REASONS = frozenset({'boot_unreadable', 'boot_invalid'})
RESERVE_BOOT_REASONS = frozenset({'reservation_boot_unverified'})
QUALIFICATION = 'stock-admission-qualified.json'
RELEASE_PROOFS = ('owner_and_token_checked', 'native_restored_and_connections_closed', 'lease_directory_absent_after_release')
ATTEMPT = re.compile(r'attempt-([0-9a-f]{16})')
BOOT = re.compile(rb'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
TIMEOUT = {'admit': 20, 'reserve': 20, 'close': 34, 'readback': 20, 'confirm-close': 28,
           'finalize': 25, 'absent': 20}
REPAIR_TIMEOUT = 94  # Bound exit + native restore and up to five guarded navigation operations.
RUN_TIMEOUT = {'locator': 115, 'measure': 281}
SETTLE = 1
READBACK_BUDGET = 2*TIMEOUT['readback'] + SETTLE
FINALIZE_BUDGET = TIMEOUT['finalize'] + TIMEOUT['absent'] + 20
AFTER_REPAIR_BUDGET = 2*READBACK_BUDGET + TIMEOUT['confirm-close'] + FINALIZE_BUDGET
CLOSE_BUDGET = {
    'locator': 2*TIMEOUT['close'] + SETTLE + FINALIZE_BUDGET,
    'measure': READBACK_BUDGET + REPAIR_TIMEOUT + AFTER_REPAIR_BUDGET,
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def durable(path, data):
    with path.open('xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save(path, data):
    durable(path, (json.dumps(data, indent=2)+'\n').encode('ascii'))


def plain(path, limit=65536):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path
            and 0 < path.stat().st_size <= limit, 'invalid_private_input')
    return path.read_bytes()


def load(path):
    return json.loads(plain(path))


def script_at(path, kind):
    source = plain(path)
    require(source.isascii() and source.endswith(b'\n')
            and source.startswith(f'#!/bin/sh\n# AIDOKU_GENERATION_STOCK {kind}: {OPERATOR};'.encode('ascii')), 'wrong_device_template')
    return source


def check_plan(root, kind):
    home = root/kind
    source = script_at(home/'device.sh', kind)
    require(source == script_at(root/f'{kind}-template.sh', kind), 'changed_device_template')
    payload = home/'payload.framed'
    require(payload.is_file() and not payload.is_symlink() and payload.resolve() == payload and
            1000 < payload.stat().st_size < 128*1024**2, 'device_payload_missing_or_invalid')
    roster = load(home/'roster.json')
    require(type(roster) is dict and all(type(n) is int and 0 < n < 55_000_000 for n in roster.values()), 'invalid_payload_roster')
    measure_leaves = {'clock', 'trace-launch.sh', 'input/dispatch.sh', 'input/tile-input', 'input/artwork.guard', 'reference/program.elf'}
    shared = {'plan-kind', 'input/navigation.sh', 'input/framewatch', 'reference/query.elf', 'reference/elf-size',
              'reference/kpm/payload.tsv', 'reference/kpm/.aidoku-installation-complete', 'reference/aidoku/launch.sh',
              'reference/aidoku/assets/aidoku-cover-v2.png', 'reference/documents/Aidoku.sh'}
    require(shared <= roster.keys() and (measure_leaves & roster.keys()) == (measure_leaves if kind == 'measure' else set()),
            'wrong_measure_or_locator_roster')
    for name in roster:
        p = PurePosixPath(name)
        require(str(p) == name and not p.is_absolute() and '..' not in p.parts and
                (name in shared|measure_leaves or re.fullmatch(r'input/(?:lib|models)/[A-Za-z0-9_.+-]+|input/(?:native-target|card-input)', name)),
                'unexpected_payload_file')
    allowed_dirs = {'fixture/imports'} | {p.as_posix() for name in roster for p in PurePosixPath(name).parents if p != PurePosixPath('.')}
    seen, files = set(), set()
    with payload.open('rb') as stream:
        require(stream.read(len(source)) == source, 'changed_framed_device_source')
        with tarfile.open(fileobj=stream, mode='r|gz') as archive:
            for member in archive:
                require(member.name not in seen, 'duplicate_payload_member')
                seen.add(member.name)
                if member.isdir():
                    require(member.name in allowed_dirs, 'unexpected_payload_directory')
                    continue
                require(member.isreg() and member.name in roster and member.size == roster[member.name], 'unexpected_payload_member')
                files.add(member.name)
                if member.name == 'plan-kind':
                    require(archive.extractfile(member).read() == (kind+'\n').encode('ascii'), 'wrong_payload_kind')
                if member.name == 'trace-launch.sh':
                    require(archive.extractfile(member).read() == plain(root/'diagnostic-launch.sh'), 'changed_scoped_launcher')
                if member.name == 'input/artwork.guard':
                    require(archive.extractfile(member).read() == plain(root/'fresh-artwork.guard', 2_000_000), 'changed_artwork_guard')
    require(files == roster.keys(), 'incomplete_payload')
    text = plain(home/'space-kib', 16)
    require(re.fullmatch(rb'[1-9][0-9]{0,5}\n', text) is not None, 'bad_space_budget')
    space = int(text)
    require((sum(roster.values())+1023)//1024 + 128*1024 == space < 400000, 'bad_space_budget')
    return source, payload, space


def verify_locator(root):
    proof = load(root/'locator-qualified.json')
    name = proof.get('locator_attempt', '')
    require(ATTEMPT.fullmatch(name) and proof == {'qualified': True, 'locator_attempt': name, 'source': SOURCE,
                                                 'diagnostic': DIAGNOSTIC}, 'invalid_locator_qualification')
    make = runpy.run_path(str(root/'prepare_stock_generation.py'))
    observed = make['locator_receipt'](root, root/name)
    require(observed.is_file() and not observed.is_symlink(), 'missing_fresh_card_observation')


def frame_decode(root, raw, kind):
    old = runpy.run_path(str(private_path('stock_templates')/'run-device.py'))
    rows, fields = old['evidence'](raw, kind)
    if kind == 'measure':
        historical = runpy.run_path(str(private_path('stock_templates')/'phase_records.py'))['attach_phases']
        adapter = runpy.run_path(str(root/'generation_phase_records.py'))
        rows = adapter['attach_generation'](raw, rows, historical)
    else:
        require(not any(line.startswith((b'LAUNCH_', b'GENERATION_', b'STOCK_BIRTH', b'STOCK_SAMPLE')) for line in raw.splitlines()),
                'unexpected_locator_timing')
    return rows, fields


def pin(base):
    cfg = base['SSH_DIR']
    args = argparse.Namespace(ssh_config=cfg/'ssh_config', known_hosts=cfg/'known_hosts', host_alias='kindle-physical')
    argv = base['ssh_argv'](args)
    expanded = subprocess.check_output([argv[0], '-G', *argv[1:]], stderr=subprocess.DEVNULL, timeout=8)
    opts = dict(line.partition(b' ')[::2] for line in expanded.splitlines())
    expected = {b'user':b'root', b'hostname':device_hostname(), b'port':b'22', b'preferredauthentications':b'password',
                b'pubkeyauthentication':b'false', b'stricthostkeychecking':b'true', b'clearallforwardings':b'yes',
                b'controlpath':b'none'}
    require(all(opts.get(key, b'none' if key == b'controlpath' else b'') == value for key,value in expected.items())
            and opts.get(b'proxycommand', b'none') == b'none' and opts.get(b'proxyjump', b'none') == b'none', 'unexpected_pinned_transport')
    askpass = private_path('askpass')
    require(askpass.is_file() and os.access(askpass, os.X_OK), 'unexpected_password_adapter')
    return argv


def ssh_transport(lease, request, argv, source, kind, label, env, operations):
    # Use the already qualified stdin bootstrap rather than a large SSH argv.
    bootstrap = ('LC_ALL=C; export LC_ALL; size=$1; shift; '
                 'source=$(dd bs=1 count="$size" 2>/dev/null && printf x) || exit 1; '
                 '[ "${#source}" -eq "$((size+1))" ] || exit 1; eval "${source%x}"')

    def exchange(action, timeout, *, payload=None, space=0, boot='-'):
        require(lease.status(request)['ownership_verified'] is True, 'lease_not_owned')
        remote = shlex.join(['sh','-c',bootstrap,'aidoku-generation-stock',str(len(source)),action,label,'stock',str(space),boot])
        operation = {'action': action}
        operations.append(operation)
        io = {'stdin': payload} if payload is not None else {'input': source}
        try:
            reply = subprocess.run([*argv, remote], **io, env=env, capture_output=True, timeout=timeout)
            status, out, err = reply.returncode, reply.stdout, reply.stderr
        except subprocess.TimeoutExpired as exc:
            status, out, err = 'timeout', exc.stdout or b'', exc.stderr or b''
        refusals = [tag.decode('ascii') for tag in re.findall(rb'^AB_REFUSED ([a-z_]{1,64})$', err, re.M)[:12]]
        operation.update(status=status, refusals=refusals)
        require(len(out) <= 7*1024**2 and len(err) <= 65536, 'oversized_device_reply')
        return status, out
    return exchange


def admission_reasons(action, kind):
    boot_reasons = ADMIT_BOOT_REASONS if action == 'admit' else RESERVE_BOOT_REASONS
    return COMMON_ADMISSION_REASONS | boot_reasons | (MEASURE_ADMISSION_REASONS if kind == 'measure' else set())


def admission_observation(action, kind, label, expected_boot, status, raw):
    # An actual refusal must be a single complete, bound frame with the exact
    # exit code. A partial reply or diagnostics from SSH are not device facts.
    if type(status) is int and status == 1:
        prefix = f'AB_ADMISSION_REFUSED {action} {kind} {label} {expected_boot} '.encode('ascii')
        if raw.startswith(prefix) and raw.endswith(b'\n'):
            token = raw[len(prefix):-1]
            if re.fullmatch(rb'[a-z0-9_]{1,64}', token) and token.decode('ascii') in admission_reasons(action, kind):
                return 'refused', token.decode('ascii')
        return 'unclassified_refusal', None
    if type(status) is int and status == 0:
        if action == 'admit':
            prefix = f'AB_ADMITTED {kind} {label} '.encode('ascii')
            valid = raw.startswith(prefix) and raw.endswith(b'\n') and BOOT.fullmatch(raw[len(prefix):-1]) is not None
        else:
            valid = raw == f'AB_RESERVED {kind} {label}\n'.encode('ascii')
        return ('passed' if valid else 'invalid_reply'), None
    return ('transport_uncertain' if status in (255, 'timeout') else 'unclassified_exit'), None


def has_terminal(raw, line):
    rows = raw.split(b'\n')
    return rows[-2:] == [line[:-1], b''] and rows.count(line[:-1]) == 1


def close_measure(perform, operations, entry, boot, begin_repair):
    def readback():
        replies = {entry('READBACK_'+state).rstrip(b'\n')+b' '+boot.encode('ascii')+b'\n': state
                   for state in ('CLOSED', 'EXACT', 'UNTOUCHED', 'PENDING')}
        for turn in range(2):
            code, data = perform('readback', TIMEOUT['readback'], boot=boot)
            if code == 0:
                observed = replies.get(data)
                if observed is not None:
                    operations[-1]['readback_state'] = observed.lower()
                return observed
            if turn == 0 and (code in (255, 'timeout') or
                              operations[-1].get('refusals') in (['operation_running'], ['no_owned_journal'])):
                time.sleep(SETTLE)
                continue
            break
        return None

    def found(state):
        if state == 'CLOSED':
            return True
        if state in ('EXACT', 'UNTOUCHED'):
            code, reply = perform('confirm-close', TIMEOUT['confirm-close'], boot=boot)
            if code == 0 and reply == entry('RESTORED'):
                return True
            # A confirmation never emits input. Prove that its close record
            # really exists if its acknowledgement was lost.
            return readback() == 'CLOSED'
        return False

    # A failed or missing run may already have attempted restoration. Observe
    # first. Any repair is fenced by the device's spent input intent and journal
    # lock, and its response is never retried with another repair request.
    state = readback()
    if state != 'PENDING' or not begin_repair():
        return found(state)
    code, reply = perform('close', REPAIR_TIMEOUT, boot=boot)
    if code == 0 and reply == entry('RESTORED'):
        return True
    return found(readback())


def unsent_plan(root, kind):
    require(not os.path.lexists(root/f'{kind}-sent.json'), 'one_visit_plan_already_consumed')


def send_journal(root, attempt, kind):
    # This file is immutable and consumes the one permitted reservation for
    # the plan. Fsync before any reserve request could leave this process.
    save(root/f'{kind}-sent.json', {'attempt': attempt.name, 'kind': kind})


def journal_matches(root, attempt, kind):
    path = root/f'{kind}-sent.json'
    if not path.exists() and not path.is_symlink():
        return False
    data = load(path)
    require(type(data) is dict and data.get('kind') == kind and ATTEMPT.fullmatch(data.get('attempt', '')) is not None,
            'invalid_mutable_intent_journal')
    return data['attempt'] == attempt.name


def untouched_reserve_proof(kind, label, boot, reason):
    return {'source': SOURCE, 'operator': OPERATOR, 'mode': kind, 'label': label, 'expected_boot': boot,
            'refused_before_journal_creation': True, 'reason': reason}


def untouched_reserve(attempt, kind, label, boot):
    path = attempt/'reserve-refused.json'
    if not path.exists() and not path.is_symlink():
        return False
    proof = load(path)
    reason = proof.get('reason')
    require(type(reason) is str and reason in admission_reasons('reserve', kind)
            and proof == untouched_reserve_proof(kind, label, boot, reason)
            and all(not os.path.lexists(attempt/name) for name in ('run-intent.json', 'positive-device-closeout.json',
                                                                'repair-sent.json')), 'invalid_saved_reserve_refusal')
    return True


def operate(root, attempt, kind, source, payload, space, base, lease, exchange_factory, decoder, *, recover=False, not_after=None):
    # Serialize the same attempt before inspecting lease ownership or its
    # mutable intent. Never unlink this lock while the attempt can be opened.
    path = attempt/'operator.lock'
    fd = os.open(path, os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_RDWR, 0o600)
    try:
        identity = os.fstat(fd)
        require(stat.S_ISREG(identity.st_mode) and identity.st_nlink == 1 and identity.st_uid == os.geteuid()
                and stat.S_IMODE(identity.st_mode) == 0o600, 'invalid_attempt_operator_lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('attempt_operator_running') from exc
        named = path.lstat()
        require((identity.st_dev, identity.st_ino) == (named.st_dev, named.st_ino), 'attempt_operator_lock_replaced')
        return operate_locked(root, attempt, kind, payload, space, base, lease, exchange_factory, decoder,
                              recover=recover, not_after=not_after)
    finally:
        os.close(fd)


def rejected_run_evidence(attempt, raw, report, restored_line):
    parser = runpy.run_path(str(private_path('stock_templates')/'failure_receipt.py'))['received_failure']
    # The inherited refusal adapter expects operation, not action. Keep its
    # strict closed-run predicates; never pass a failure to the timing decoder.
    legacy_report = report | {'operations': [row | {'operation':row['action']} for row in report['operations']]}
    result = parser(raw.replace(restored_line, b'AB_CLOSEOUT restored\n'), legacy_report)
    if result is not None:
        save(attempt/'failure-diagnostic.json', result)
    return result is not None


def operate_locked(root, attempt, kind, payload, space, base, lease, exchange_factory, decoder, *, recover, not_after):
    label = ATTEMPT.fullmatch(attempt.name)[1]
    owner = f'aidoku-stock-generation-{label}'
    request = lease.LeaseRequest(base['LEASE'], attempt/'owner.token', owner, owner)
    operations = []
    exchange = exchange_factory(request, operations)
    report = {'source': SOURCE, 'diagnostic': DIAGNOSTIC, 'operator': OPERATOR, 'mode': kind,
              'action': 'recover' if recover else 'run', 'complete': False, 'sample_count': 0,
              'operations': operations, 'physical_first_screen_measured': False,
              'temporary_launcher_mount_permitted': kind == 'measure', 'installation_write_requested': False}
    entry = lambda word: f'AB_{word} {kind} {label}\n'.encode('ascii')
    held = mutate = positive = False
    raw, status = b'', None
    closeout = None
    boot = None
    boot_record = attempt/'admitted-boot.json'
    mutable_path = root/f'{kind}-sent.json'
    start = time.monotonic()
    limit = (CLOSE_BUDGET[kind] if recover else
             (not_after-datetime.now(timezone.utc)).total_seconds() if not_after is not None else 0)

    def require_budget(seconds):
        require(time.monotonic()-start+seconds <= limit and (recover or
                (not_after is not None and (not_after-datetime.now(timezone.utc)).total_seconds() >= seconds)),
                'remaining_window_too_short')

    def perform(action, seconds, **params):
        require_budget(seconds)
        status, raw = exchange(action, seconds, **params)
        if action in ('admit', 'reserve'):
            state, reason = admission_observation(action, kind, label, params.get('boot', '-'), status, raw)
            operations[-1]['admission_state'] = state
            if reason is not None:
                operations[-1]['admission_reason'] = reason
        return status, raw

    def begin_repair():
        path = attempt/'repair-sent.json'
        intent = {'mode':kind, 'label':label, 'boot_id':boot, 'input_may_have_been_requested':True}
        if path.exists() or path.is_symlink():
            require(load(path) == intent, 'wrong_saved_repair_intent')
            return False
        require_budget(REPAIR_TIMEOUT + AFTER_REPAIR_BUDGET)
        save(path, intent)
        return True

    try:
        if recover:
            require(lease.status(request)['ownership_verified'] is True, 'same_owner_recovery_not_verified')
            held = True
            # Stay fail-closed even if a malformed durable journal cannot be read.
            mutate = mutable_path.exists() or mutable_path.is_symlink()
            if mutate:
                require(journal_matches(root, attempt, kind), 'mutable_journal_belongs_to_another_attempt')
                boot = load(boot_record)['boot_id']
                require(type(boot) is str and BOOT.fullmatch(boot.encode('ascii')), 'invalid_admitted_boot')
        else:
            with payload.open('rb') as wire:
                require_budget(TIMEOUT['admit'])
                with base['block_interrupts']():
                    try:
                        lease.acquire(request)
                    except Exception as exc:
                        details = getattr(exc, 'details', {})
                        creation = ('lease_directory_created', 'private_token_created', 'owner_record_created')
                        if all(details.get(key) is False for key in creation):
                            if getattr(exc, 'code', None) == 'lease_exists':
                                report['equipment_busy'] = True
                        else:
                            report['partial_acquire'] = True
                            try:
                                held = lease.status(request)['ownership_verified'] is True
                            except Exception:
                                pass
                        raise
                    held = True
                # The CLI's off-lease check may precede another successful
                # dispatch. The authoritative lease now serializes this check
                # with all possible writers of the one-use intent.
                unsent_plan(root, kind)
                _, offered = perform('admit', TIMEOUT['admit'], space=space)
                require(operations[-1]['admission_state'] == 'passed', 'read_only_admission_failed')
                boot = offered[:-1].rsplit(b' ', 1)[-1].decode('ascii')
                save(boot_record, {'boot_id': boot})
                require_budget(TIMEOUT['reserve'] + RUN_TIMEOUT[kind] + CLOSE_BUDGET[kind])
                # Mark potential mutation even if an interrupt lands during
                # the fsync: no unsafe release on an uncertain local journal.
                mutate = True
                send_journal(root, attempt, kind)
                perform('reserve', TIMEOUT['reserve'], space=space, boot=boot)
                reserved = operations[-1]
                require(reserved['admission_state'] == 'passed', 'reservation_not_acknowledged')
                require_budget(RUN_TIMEOUT[kind] + CLOSE_BUDGET[kind])
                save(attempt/'run-intent.json', {'mode': kind, 'run_may_have_been_sent': True})
                status, raw = perform('run', RUN_TIMEOUT[kind], payload=wire, space=space, boot=boot)
                positive = type(status) is int and 0 <= status < 255 and has_terminal(raw, entry('RESTORED'))
    except (Exception, KeyboardInterrupt) as exc:
        report['error'] = base['safe_reason'](exc)
        if recover and not held:
            report['unresolved_recovery'] = True
    finally:
        if held:
            try:
                with base['block_interrupts']():
                    refused_before_reserve = False
                    if mutate:
                        require(journal_matches(root, attempt, kind) and type(boot) is str and
                                BOOT.fullmatch(boot.encode('ascii')) and load(boot_record) == {'boot_id':boot}, 'mutable_intent_not_durable')
                        # Persist a fully validated refusal here, with signals
                        # blocked, even if interrupted just after the reply.
                        if operations and operations[-1]['action'] == 'reserve' and operations[-1].get('admission_state') == 'refused':
                            save(attempt/'reserve-refused.json', untouched_reserve_proof(kind, label, boot, operations[-1]['admission_reason']))
                        refused_before_reserve = untouched_reserve(attempt, kind, label, boot)
                    if mutate and not refused_before_reserve:
                        receipt = attempt/'positive-device-closeout.json'
                        proof = {'outcome':'restored', 'mode':kind, 'label':label, 'source':SOURCE, 'boot_id':boot}
                        if receipt.exists() or receipt.is_symlink():
                            require(load(receipt) == proof, 'wrong_saved_native_restoration')
                            positive = True
                        if not positive and kind == 'measure':
                            positive = close_measure(perform, operations, entry, boot, begin_repair)
                        elif not positive:
                            for turn in range(2):
                                code, restored = perform('close', TIMEOUT['close'], boot=boot)
                                if code == 0 and restored == entry('RESTORED'):
                                    positive = True
                                    break
                                if turn or operations[-1].get('refusals') not in (['operation_running'], ['no_owned_journal']):
                                    break
                                time.sleep(SETTLE)
                        require(positive, 'positive_owned_restoration_missing')
                        if not receipt.exists():
                            save(receipt, proof)
                        require_budget(TIMEOUT['finalize'] + TIMEOUT['absent'] + 5)
                        code, removed = perform('finalize', TIMEOUT['finalize'], boot=boot)
                        exact = code == 0 and removed == entry('FINALIZED')
                        ambiguous = code in (255, 'timeout') or (code == 1 and operations[-1].get('refusals') == ['no_owned_journal'])
                        require(exact or ambiguous, 'owned_finalization_failed')
                        code, empty = perform('absent', TIMEOUT['absent'], boot=boot)
                        require(code == 0 and empty == entry('ABSENT'), 'independent_owned_absence_failed')
                        closeout = 'restored'
                    elif refused_before_reserve:
                        closeout = 'untouched_reservation_refused'
                    else:
                        closeout = 'untouched_read_only_admission'
                    report['release_unverified'] = True
                    full = lease.release(request, native_restored_and_connections_closed=True)
                    held = False
                    released = {k:full[k] for k in ('state','released_at_utc',*RELEASE_PROOFS)}
                    require(released['state'] == 'released' and all(released[k] is True for k in RELEASE_PROOFS), 'lease_release_unverified')
                    save(attempt/'released.json', released)
                    report['release'] = released
                    report.pop('release_unverified')
            except (Exception, KeyboardInterrupt) as exc:
                report['close_error'] = base['safe_reason'](exc)
    report.update(closeout=closeout, run_status=status,
                  recovery_required=(held or report.get('release_unverified', False) or report.get('unresolved_recovery', False)
                                     or (report.get('partial_acquire', False) and 'release' not in report)))
    # Do not parse samples, classify pixels, or write reports under a lease.
    if not report['recovery_required'] and 'release' in report and not recover and closeout == 'restored':
        if status != 0 or not has_terminal(raw, entry('RESTORED')):
            report['error'] = 'incomplete_device_visit'
            if type(status) is int and 0 < status < 255 and kind == 'measure':
                try:
                    if rejected_run_evidence(attempt, raw, report, entry('RESTORED')):
                        report['failure_diagnostic_saved'] = True
                except (ValueError, OSError) as exc:
                    report['failure_evidence_error'] = base['safe_reason'](exc)
        else:
            try:
                rows, fields = decoder(raw, kind)
                if kind == 'measure':
                    require(len(rows) == 1 and raw.splitlines().count(b'STOCK_PROGRAM_IDENTICAL') == 1
                            and set(fields) == {('trace','control-01'), ('library','control-01')}, 'unqualified_measurement')
                    report['published_program_identical'] = True
                    save(attempt/'clock-records.json', rows)
                else:
                    require(not rows and set(fields)=={('stock','locator')}, 'unqualified_locator')
                for (leaf, name), data in fields.items():
                    durable(attempt/(name+('.trace' if leaf == 'trace' else '.frame')), data)
                report.update(complete=True, sample_count=len(rows))
            except (ValueError, OSError) as exc:
                report['evidence_error'] = base['safe_reason'](exc)
    if recover and 'release' in report and not report['recovery_required']:
        report['complete'] = True
    save(attempt/(f'recovery-{secrets.token_hex(4)}.json' if recover else 'result.json'), report)
    return report


def minimum_visit_seconds(kind):
    return TIMEOUT['admit'] + TIMEOUT['reserve'] + RUN_TIMEOUT[kind] + CLOSE_BUDGET[kind] + 10


def require_permission_deadline(deadline, kind, now):
    require(deadline.tzinfo and (deadline-now).total_seconds() >= minimum_visit_seconds(kind), 'approval_expired_or_too_short')


def main():
    os.umask(0o077)
    require(HERE.parent == Path('/root') and HERE.name.startswith('aidoku-stock-generation-20261127.') and
            not HERE.stat().st_mode & 0o077, 'wrong_generation_stock_workspace')
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    for name in ('preflight','run'):
        cmd = actions.add_parser(name)
        cmd.add_argument('kind', choices=KINDS)
        if name == 'run':
            cmd.add_argument('--confirmed-one-physical-visit', action='store_true', required=True)
            cmd.add_argument('--approval-expires', type=datetime.fromisoformat, required=True,
                             help='Expiry of this approval, not a device reservation.')
            cmd.add_argument('--max-visit-seconds', type=int, required=True,
                             help='Execution budget from dispatch, including bounded recovery; does not book the device.')
    actions.add_parser('recover').add_argument('attempt', type=Path)
    args = parser.parse_args()
    base = runpy.run_path(str(private_path('base_runner')))
    argv = pin(base)
    recovering = args.action == 'recover'
    if recovering:
        attempt = args.attempt
        match = ATTEMPT.fullmatch(attempt.name)
        require(attempt.parent == HERE and match and attempt.resolve(strict=True) == attempt and
                not (attempt/'released.json').exists(), 'invalid_same_owner_attempt')
        stored = load(attempt/'identity.json')
        kind = stored['kind']
        require(kind in KINDS and stored == {'label':match[1], 'kind':kind, 'source':SOURCE}, 'recovery_identity_changed')
        source, payload, space = script_at(attempt/'device.sh', kind), None, 0
    else:
        kind = args.kind
        if kind == 'measure':
            verify_locator(HERE)
        source, payload, space = check_plan(HERE, kind)
        if args.action == 'preflight':
            subprocess.run(['sh','-n'], input=source, check=True)
            print(json.dumps({'source':SOURCE, 'diagnostic':DIAGNOSTIC, 'kind':kind, 'host_preflight':True,
                              'physical_access':False, 'lease_acquired':False, 'space_kib':space, 'framed_bytes':payload.stat().st_size,
                              'minimum_visit_seconds':minimum_visit_seconds(kind), 'device_reserved':False}))
            return 0
        qualification = load(HERE/QUALIFICATION)
        require(qualification['source'] == SOURCE and qualification['diagnostic'] == DIAGNOSTIC
                and qualification['operator'] == OPERATOR and qualification['physical_access'] is False
                and all(qualification[key] is True for key in ('both_integrated_device_variants_verified',
                    'linux_disconnection_fixtures_passed', 'guarded_arm_home_to_library_recovery_verified',
                    'read_only_admission_fixtures_passed')), 'operator_not_qualified_off_device')
        require(args.max_visit_seconds >= minimum_visit_seconds(kind), 'invalid_visit_budget')
        require_permission_deadline(args.approval_expires, kind, datetime.now(timezone.utc))
        unsent_plan(HERE, kind)
        label = secrets.token_hex(8)
        attempt = HERE/f'attempt-{label}'
        attempt.mkdir(mode=0o700)
        save(attempt/'identity.json', {'label':label, 'kind':kind, 'source':SOURCE})
        durable(attempt/'device.sh', source)
    secret = sys.stdin.buffer.readline(128)
    value = secret.rstrip(b'\r\n')
    require(secret.endswith(b'\n') and not sys.stdin.buffer.read(1) and 0 < len(value) < 65
            and all(32 <= c < 127 for c in value), 'invalid_private_credential_frame')
    env = dict(os.environ, AIDOKU_TEST_PASSWORD=value.decode('ascii'), SSH_ASKPASS=str(private_path('askpass')), SSH_ASKPASS_REQUIRE='force')
    del secret, value
    deadline = None
    if not recovering:
        # Start the execution budget only when ready to attempt the lease.
        # Expired approval or a slow credential provider cannot book it.
        now = datetime.now(timezone.utc)
        require_permission_deadline(args.approval_expires, kind, now)
        deadline = now + timedelta(seconds=min(args.max_visit_seconds, (args.approval_expires-now).total_seconds()))
    for sig in base['SIGNALS']:
        signal.signal(sig, base['interrupt'])
    lease = base['load_lease']()
    factory = lambda request, ops: ssh_transport(lease, request, argv, source, kind,
                                                ATTEMPT.fullmatch(attempt.name)[1], env, ops)
    report = operate(HERE, attempt, kind, source, payload, space, base, lease, factory,
                     lambda raw, visit_kind: frame_decode(HERE, raw, visit_kind), recover=recovering,
                     not_after=deadline)
    print(json.dumps(report), flush=True)
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
