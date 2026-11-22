"""Audit four completed visits, retire the one consumed upload and publish a return receipt."""

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


WORK = Path('/var/tmp/aidoku-library-transition-20261122-final')
SHARED = Path('/var/tmp/kindle-build-coordination')
JOURNAL = 'transition-closeout-intent.json'
LIMIT = 256 * 1024**2
FLAGS = {'ordinary_exit', 'native_restored', 'close', 'absent', 'connections_closed', 'owner_and_token_checked', 'released'}


def require(condition):
    if not condition:
        raise ValueError('unverified transition closeout')


def directory(path):
    info = path.lstat()
    require(path.is_absolute() and path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)


def regular(path, limit):
    info = path.lstat()
    require(path.resolve(strict=True) == path and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == os.geteuid() and 0 < info.st_size <= limit)
    return info


def load_record(path):
    def no_duplicates(pairs):
        require(len(pairs) == len(dict(pairs)))
        return dict(pairs)
    regular(path, 256 * 1024)
    return json.loads(path.read_bytes(), object_pairs_hook=no_duplicates)


def audit_module():
    spec = importlib.util.spec_from_file_location('transition_closeout_audit', Path(__file__).with_name('audit.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:\+00:00|Z)', value))
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.utcoffset() == timedelta(0) and stamp <= datetime.now(timezone.utc))
    return stamp


def final_card_release(work):
    for name in ('plan', 'visits', 'receipts'):
        directory(work / name)
    require({entry.name for entry in (work / 'receipts').iterdir()} == {f'{slot:02}.json' for slot in range(1, 5)})
    slots, last_observed = {}, []
    for entry in (work / 'visits').iterdir():
        directory(entry)
        match = re.fullmatch(r'0([1-4])-([0-9a-f]{16})', entry.name)
        require(match)
        slot = int(match[1])
        report = load_record(entry / 'operations.json')
        release = report.get('release')
        if release is not None:
            stamp = timestamp(release['released_at_utc'])
            last_observed.append(stamp)
        if os.path.lexists(entry / 'card-intent.json'):
            require(release is not None and slot not in slots and report.get('phase') == 'release')
            slots[slot] = (stamp, release['released_at_utc'])
    require(set(slots) == set(range(1, 5)) and all(slots[index][0] < slots[index + 1][0] for index in range(1, 4))
            and max(last_observed) == slots[4][0])
    return slots[4]


def audited_return(work):
    last_release = final_card_release(work)
    bundle = audit_module().audit(work, 4)
    counters, rows = bundle['audit'], bundle['receipts']
    require(counters['checked_visits'] == counters['replayed_endpoints'] == counters['replayed_transition_records'] == 4
            and bundle['analysis']['complete_endpoints'] is True and bundle['analysis']['measured_initial_to_library'] == 3
            and len(rows) == 4 and all(row['slot'] == slot and row['configuration'] == 'diagnostic'
                and row['result'] == 'measured' and row['transition'] is not None and row['interval'] is not None
                and set(row['closeout']) == FLAGS and all(flag is True for flag in row['closeout'].values())
                for slot, row in enumerate(rows, 1)))
    return last_release


def identity(info):
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
            and info.st_uid == os.geteuid() and 0 < info.st_size <= LIMIT)
    return {key: getattr(info, 'st_' + key) for key in ('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns')}


def write_new(parent_fd, name, data):
    create_file(parent_fd, name, data)
    os.fsync(parent_fd)


def create_file(parent_fd, name, data):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    with os.fdopen(descriptor, 'wb') as writer:
        writer.write(data)
        writer.flush()
        os.fsync(writer.fileno())


def publish_once(shared_fd, name, data):
    temporary = '.' + name + '.pending-' + os.urandom(8).hex()
    create_file(shared_fd, temporary, data)
    try:
        os.link(temporary, name, src_dir_fd=shared_fd, dst_dir_fd=shared_fd, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=shared_fd)
        os.fsync(shared_fd)


def encode(value):
    return (json.dumps(value, indent=2, allow_nan=False) + '\n').encode('ascii')


def finalize_authorized(work, shared, apply, *, journal_name, receipt_prefix, audit_return, make_receipt):
    directory(work)
    directory(shared)
    released, raw_time = audit_return(work)
    local_time = released.astimezone(timezone(timedelta(hours=8)))
    name = receipt_prefix + local_time.strftime('%Y%m%d-%H%M%S') + '.json'
    target = shared / name
    payload = work / 'plan/payload.tar.gz'
    journal = work / journal_name
    started = os.path.lexists(journal)
    present = os.path.lexists(payload)
    payload_identity = identity(regular(payload, LIMIT)) if present else None
    if started:
        saved = load_record(journal)
        require(set(saved) == {'schema', 'target', 'released_at_utc', 'payload'} and saved['schema'] == 1
                and saved['target'] == name and saved['released_at_utc'] == raw_time and isinstance(saved['payload'], dict)
                and set(saved['payload']) == {'dev', 'ino', 'size', 'mtime_ns', 'ctime_ns'}
                and all(type(number) is int for number in saved['payload'].values())
                and 0 < saved['payload']['size'] <= LIMIT and (not present or saved['payload'] == payload_identity))
        payload_identity = saved['payload']
    require(payload_identity is not None)
    receipt = make_receipt(raw_time, payload_identity['size'])
    if os.path.lexists(target):
        regular(target, 8192)
        require(started and not present and target.read_bytes() == encode(receipt))
        return {'state': 'already_published', 'receipt': name, 'last_released_at_utc': raw_time}
    status = {'state': 'ready_dry_run', 'consumed_upload_bytes': payload_identity['size'],
              'receipt': name, 'last_released_at_utc': raw_time}
    if not apply:
        return status
    descr = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW
    work_fd = os.open(work, descr)
    try:
        plan_fd = os.open('plan', descr, dir_fd=work_fd)
        try:
            shared_fd = os.open(shared, descr)
            try:
                if not started:
                    write_new(work_fd, journal_name, encode({'schema': 1, 'target': name, 'released_at_utc': raw_time,
                                                             'payload': payload_identity}))
                if present:
                    require(identity(os.stat('payload.tar.gz', dir_fd=plan_fd, follow_symlinks=False)) == payload_identity)
                    os.unlink('payload.tar.gz', dir_fd=plan_fd)
                    os.fsync(plan_fd)
                require(not os.path.lexists(payload))
                publish_once(shared_fd, name, encode(receipt))
            finally:
                os.close(shared_fd)
        finally:
            os.close(plan_fd)
    finally:
        os.close(work_fd)
    return {**status, 'state': 'published'}


def receipt_record(raw_time, payload_size):
    return {'schema': 1, 'study': 'Aidoku normal-card Library transition diagnostic', 'completed_visits': 4,
            'excluded_warmups': 1, 'formal_visits': 3, 'all_visits_exited_normally': True,
            'all_native_restores_and_independent_absence_checks_passed': True,
            'all_connections_closed_and_original_owner_token_releases_verified': True,
            'independently_replayed_business_endpoints_and_transitions': 4, 'last_released_at_utc': raw_time,
            'removed_consumed_upload_bytes': payload_size, 'small_evidence_retained': True,
            'handoff': 'Historical return only; not a current availability check or reservation.'}


def finalize(work, shared, apply=False):
    require(work == WORK and shared == SHARED)
    return finalize_authorized(work, shared, apply, journal_name=JOURNAL,
                               receipt_prefix='aidoku-library-transition-return-', audit_return=audited_return,
                               make_receipt=receipt_record)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Default; validate without writing or unlinking')
    mode.add_argument('--apply', action='store_true', help='Retire only the consumed payload and publish once')
    args = parser.parse_args()
    try:
        print(json.dumps(finalize(WORK, SHARED, args.apply), allow_nan=False))
    except Exception:
        sys.exit('Closeout not verified; inspect privately. No return receipt was overwritten.')
