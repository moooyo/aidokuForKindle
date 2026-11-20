"""Freeze or operate one normal-entry study; freezing never opens the Kindle."""

import argparse
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import sys
import tarfile

from transport import Device, SIGNALS, interrupt, saved


def visits(pictures_first=False):
    result = ['control-warmup', 'candidate-warmup']
    for pair in range(1, 7):
        order = ('control', 'candidate') if pair % 2 else ('candidate', 'control')
        result.extend(f'{side}-{pair:02}' for side in order)
    pictures = ['control-picture', 'candidate-picture']
    return pictures + result if pictures_first else result + pictures


def cases_for(mode, pictures_first=False):
    if mode == 'tapcheck':
        return ['control-warmup']
    return visits(pictures_first) if mode == 'study' else [mode]


def valid_mode(mode, origin, reference, tap_files):
    if mode == 'reference':
        return origin == 'indexed-uri' and reference is None and not tap_files
    if mode == 'navigation':
        return origin == 'stock-navigation' and reference is None and bool(tap_files)
    if mode == 'tapcheck':
        required = {'input/navigation.sh', 'input/dispatch.sh', 'input/artwork.guard', 'input/tile-input'}
        return origin == 'stock-tap' and bool(reference) and isinstance(tap_files, dict) and required <= tap_files.keys()
    return (mode == 'study' and origin in ('stock-tap', 'indexed-uri') and bool(reference) and
            bool(tap_files) == (origin == 'stock-tap'))


def sources(work, mode, tap_inputs=None):
    operator = work/'operator'
    files = {name: operator/name for name in ('guard/guard.sh', 'visit/visit.sh', 'observer/framewatch')}
    if mode == 'study':
        files.update({'programs/programs.sh': operator/'programs/programs.sh',
                      'birth_ticks.sh': operator/'birth_ticks.sh',
                      'rename-new': operator/'programs/payload/rename-new'})
    if mode in ('study', 'tapcheck'):
        files['control/program-modes'] = work/'payload/control/program-modes'
        for side in (('control', 'candidate') if mode == 'study' else ('control',)):
            for name in ('launch.sh', 'app/aidoku', 'app/aidoku-startup-portrait'):
                path = side + '/' + name
                files[path] = work/'payload'/path
    if tap_inputs is not None:
        for name, path in tap_inputs.items():
            if (not re.fullmatch(r'input/(?:[a-z][a-z0-9.-]{1,48}|lib/[a-zA-Z0-9_+.\-]{1,80}|models/(?:eng|chi_sim)\.traineddata)', name)
                    or name in files):
                raise ValueError('invalid_input_member')
            files[name] = Path(path)
    return files


def regular(path, limit):
    if path.resolve(strict=True) != path or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('invalid_frozen_input_' + path.name)
    return path


def tapcheck_inputs(work, reference, tap_files):
    navigation = Path(tap_files['input/artwork.guard']).parent
    for mode, folder in (('reference', reference), ('navigation', navigation)):
        if folder.parent != work/'batches' or not re.fullmatch(mode + '-[0-9a-f]{16}', folder.name):
            raise ValueError('unbound_tapcheck_'+mode)
        receipt = json.loads(regular(folder/'receipt.json', 256 * 1024).read_bytes())
        if (receipt.get('ok') is not True or receipt.get('mode') != mode or receipt.get('completed') != [mode]
                or receipt.get('release', {}).get('owner_and_token_checked') is not True
                or receipt['release'].get('lease_directory_absent_after_release') is not True):
            raise ValueError('unclosed_tapcheck_'+mode)
    if (str(navigation/'artwork.guard') != tap_files['input/artwork.guard'] or
            json.loads(regular(navigation/'tap-files.json', 32 * 1024).read_bytes()) != tap_files):
        raise ValueError('unbound_tapcheck_target')
    return regular(reference/'rule', 16 * 1024).read_bytes()


def verify_payload(payload, files):
    remaining = set(files)
    with tarfile.open(payload, 'r|gz') as archive:
        for item in archive:
            if (item.name not in remaining or not item.isreg() or item.mode != 0o755 or item.sparse or
                    item.size != files[item.name].stat().st_size):
                raise ValueError('unexpected_archive_member')
            with files[item.name].open('rb') as original:
                stored = archive.extractfile(item)
                while True:
                    portion = original.read(1024 * 1024)
                    if portion != stored.read(1024 * 1024):
                        raise ValueError('different_payload_member')
                    if not portion:
                        break
            remaining.remove(item.name)
    if remaining:
        raise ValueError('missing_payload_members')


def freeze(work, mode, origin, reference=None, tap_files=None, *, pictures_first=False):
    if not valid_mode(mode, origin, reference, tap_files):
        raise ValueError('invalid_reference_or_tap_inputs')
    if pictures_first and (mode, origin) != ('study', 'stock-tap'):
        raise ValueError('pictures_first_requires_stock_tap_study')
    if mode == 'tapcheck':
        tapcheck_inputs(work, reference, tap_files)
    folder = work/'plans'/('run-' + secrets.token_hex(8))
    folder.mkdir(mode=0o700)
    files = sources(work, mode, tap_files)
    for path in files.values():
        regular(path, 96 * 1024**2)
    with tarfile.open(folder/'payload.tar.gz', 'x:gz', compresslevel=2, format=tarfile.USTAR_FORMAT) as archive:
        for name, path in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = path.stat().st_size, 0o755
            with path.open('rb') as handle:
                archive.addfile(member, handle)
    verify_payload(folder/'payload.tar.gz', files)
    for name in ('device.sh', 'guard/guard.sh'):
        shutil.copyfile(regular(work/'operator'/name, 128 * 1024), folder/Path(name).name)
    if reference:
        shutil.copyfile(regular(reference/'rule', 16 * 1024), folder/'rule')
        for name in ('observer.py',):
            shutil.copyfile(work/'operator/observer'/name, folder/name)
    plan = {'label': folder.name[4:], 'mode': mode, 'origin': origin,
            'cases': cases_for(mode, pictures_first), 'reference': str(reference) if reference else None,
            'tap_files': tap_files or {}, 'sources': {name: str(path) for name, path in files.items()}}
    if mode == 'study':
        plan['schedule'] = 'pictures-first' if pictures_first else 'pictures-last'
        with (folder/'schedule').open('x') as marker:
            marker.write(plan['schedule'] + '\n')
    saved(folder/'plan.json', plan)
    return folder


def command(script, args):
    if not 0 < len(script) <= 128 * 1024:
        raise ValueError('invalid_shell_size')
    return f'exec sh -c "$(dd bs=1 count={len(script)} 2>/dev/null)" sh ' + shlex.join(args)


def archive_members(case):
    if case == 'navigation':
        return {'out/navigation/' + name for name in ('library.frame', 'qualified')}
    images = (['library-a.frame', 'library-b.frame'] if case == 'reference' else
              ['final.frame'] if case.endswith('picture') else ['trace'])
    return {'out/' + case + '/' + name for name in ['stock.frame', 'origin', 'anchor', 'qualified', *images]}


def preserve_case(folder, case, data):
    expected = archive_members(case)
    if not 0 < len(data) <= 23 * 1024**2:
        raise ValueError('invalid_case_archive_size')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r|gz') as archive:
        for item in archive:
            if item.name not in expected or not item.isreg() or item.sparse or not 0 < item.size <= 5_000_000:
                raise ValueError('invalid_case_archive_member')
            expected.remove(item.name)
        if expected:
            raise ValueError('incomplete_case_archive')
    with (folder/(case+'.tar.gz')).open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def operate(work, plan_dir, owner, recover=False):
    plan = json.loads((plan_dir/'plan.json').read_text())
    mode, origin, label = (plan[key] for key in ('mode', 'origin', 'label'))
    schedule = plan.get('schedule')
    pictures_first = schedule == 'pictures-first'
    if (plan_dir != work/'plans'/('run-'+label) or plan['cases'] != cases_for(mode, pictures_first) or
            not valid_mode(mode, origin, plan['reference'], plan['tap_files'])):
        raise ValueError('invalid_plan')
    marker = plan_dir/'schedule'
    if 'schedule' in plan:
        if (mode != 'study' or schedule not in ('pictures-first', 'pictures-last') or
                (pictures_first and origin != 'stock-tap') or
                regular(marker, 32).read_bytes() != (schedule + '\n').encode()):
            raise ValueError('invalid_frozen_schedule')
    elif marker.exists() or marker.is_symlink():
        raise ValueError('missing_frozen_schedule')
    batch = work/'batches'/f'{mode}-{label}'
    scripts = {}
    for key, source in [('device.sh', 'device.sh'), ('guard.sh', 'guard/guard.sh')]:
        scripts[key] = regular(plan_dir/key, 128 * 1024).read_bytes()
        if scripts[key] != regular(work/'operator'/source, 128 * 1024).read_bytes():
            raise ValueError('different_frozen_'+key)
    if not recover:
        if mode == 'tapcheck' and (tapcheck_inputs(work, Path(plan['reference']), plan['tap_files']) !=
                                  regular(plan_dir/'rule', 16 * 1024).read_bytes() or
                                  regular(plan_dir/'observer.py', 128 * 1024).read_bytes() !=
                                  regular(work/'operator/observer/observer.py', 128 * 1024).read_bytes()):
            raise ValueError('different_frozen_tapcheck_inputs')
        files = sources(work, mode, plan['tap_files'])
        if {key: str(path) for key, path in files.items()} != plan['sources']:
            raise ValueError('input_plan_mismatch')
        verify_payload(plan_dir/'payload.tar.gz', files)
        upload = (plan_dir/'payload.tar.gz').read_bytes()
    device = Device(work, mode, owner, label, batch if recover else None)
    if 'schedule' in plan:
        device.record['schedule'] = schedule
    receipt = batch/('recovery-'+secrets.token_hex(4)+'.json' if recover else 'receipt.json')
    if recover:
        if not (batch/'started').is_file() or not device.lease.status(device.request)['ownership_verified']:
            raise ValueError('no_matching_recovery_lease')
        device.held = True

    def call(op, args=(), *, name=None, data=b'', script='device.sh', limit=60, binary=False):
        body = scripts[script]
        output = device.call(name or op, command(body, [op, label, *args]), body + data, limit)
        if binary:
            return output
        values = output.decode('ascii').strip().splitlines()
        if not values or len(values) > 16 or not all(re.fullmatch('[a-z0-9_ -]{1,128}', item) for item in values):
            raise ValueError('unexpected_response_'+op)
        print(op, ' '.join(args), values[-1], flush=True)
        device.record['operations'][-1]['observed'] = values
        return values[-1]

    updated = recover
    success = False
    completed = []
    try:
        if not recover:
            with (batch/'started').open('x') as out:
                out.write('new frozen batch\n')
            device.acquire()
            if call('admit', script='guard.sh', limit=15) != 'guard_admitted':
                raise RuntimeError('admission_not_confirmed')
            updated = True
            if call('prepare', [mode], data=upload, limit=210) != 'device_prepared':
                raise RuntimeError('unconfirmed_prepare')
            for case in plan['cases']:
                if mode in ('reference', 'navigation'):
                    result = call(mode, limit=120)
                    expected = 'device_' + mode
                else:
                    op = 'picture' if case.endswith('picture') else 'case'
                    result = call(op, [case, origin], name=case, limit=100)
                    expected = 'device_' + op
                if result != expected:
                    raise RuntimeError('unconfirmed_'+case)
                preserve_case(batch, case, call('collect-case', [case], name='collect-'+case, binary=True, limit=100))
                if call('discard-case', [case], limit=25) != 'device_discarded':
                    raise RuntimeError('discard_not_confirmed')
                completed.append(case)
            success = True
    except (Exception, KeyboardInterrupt) as error:
        device.record['error'] = getattr(error, 'code', str(error) or type(error).__name__)
    finally:
        if device.held:
            if updated:
                try:
                    result = call('close', limit=120)
                    if result not in ('device_closed', 'stage_absent'):
                        raise RuntimeError('unverified_close')
                    updated = False
                except (Exception, KeyboardInterrupt) as error:
                    device.record['close_error'] = str(error) or type(error).__name__
            if not updated:
                device.release()
        device.record['completed'] = completed
        device.record['ok'] = bool(success and not updated and not device.held)
        saved(receipt, device.record)
        print(receipt, 'passed' if device.record['ok'] else 'check receipt', flush=True)
    return device.record['ok'] if not recover else not device.held


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    commands = parser.add_subparsers(dest='operation', required=True)
    prepare = commands.add_parser('freeze')
    prepare.add_argument('mode', choices=('reference', 'navigation', 'study', 'tapcheck'))
    prepare.add_argument('origin', choices=('indexed-uri', 'stock-tap', 'stock-navigation'))
    prepare.add_argument('--reference', type=Path)
    prepare.add_argument('--tap-files', type=Path)
    prepare.add_argument('--pictures-first', action='store_true',
                         help='Collect both unmeasured pictures before warmups in a new stock-tap study.')
    for action in ('run', 'recover'):
        launch = commands.add_parser(action)
        launch.add_argument('plan', type=Path)
        launch.add_argument('owner')
    args = parser.parse_args()
    os.umask(0o077)
    if args.operation == 'freeze':
        inputs = json.loads(args.tap_files.read_text()) if args.tap_files else None
        print(freeze(args.work, args.mode, args.origin, args.reference, inputs, pictures_first=args.pictures_first)); return 0
    for signum in SIGNALS:
        signal.signal(signum, interrupt)
    return 0 if operate(args.work, args.plan, args.owner, args.operation == 'recover') else 1


if __name__ == '__main__':
    raise SystemExit(main())
