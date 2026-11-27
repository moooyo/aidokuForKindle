#!/usr/bin/env python3
"""Prepare a fresh Scribe locator and separately seal one generation-trace visit."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import struct
import subprocess
import tarfile
import zipfile


HERE = Path(__file__).resolve().parent
private_path = runpy.run_path(str(HERE/'stock_private_inputs.py'))['private_path']
SOURCE = 'f3e5070577e74a29552f772491edac660ec62a53'
DIAGNOSTIC = 'launcher-generation-v1'
OPERATOR = 'stock-admission-v3'
QUERY = 'aidoku/app/aidoku-native-query'
APP = 'aidoku/app/aidoku'
RELEASE_PROOFS = ('owner_and_token_checked', 'native_restored_and_connections_closed', 'lease_directory_absent_after_release')
LABEL = re.compile(r'attempt-[0-9a-f]{16}')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def plain(path, limit):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path and 0 < path.stat().st_size <= limit,
            'unexpected_private_artifact')
    return path.read_bytes()


def write(path, data):
    with path.open('xb') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def change(text, old, new):
    require(text.count(old) == 1, 'unexpected_stock_source_anchor')
    return text.replace(old, new, 1)


def annotate_native_admission(text):
    start = text.index('native() (\n')
    end = text.index('\nstock_original() (\n', start)
    original = text[start:end]
    annotated = change(original, 'native() (\n', '''native() (
    native_reject() {
        if [ "${1:-}" = admission ]; then printf '%s\\n' "$2" >&8; fi
        exit 1
    }
''')
    annotated = change(annotated, '''    absent /var/local/aidoku-emulator-id && [ -c /dev/fb0 ] && no_reader aidoku weread bilicomics || exit 1
    [ "$(lipc-get-prop -s "$power" state)" = active ] &&
        [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || exit 1
    case "$(status statusbar)" in *start/running*) ;; *) exit 1;; esac
    managers=$(pidof awesome) && [ -n "$managers" ] || exit 1
''', '''    absent /var/local/aidoku-emulator-id && [ -c /dev/fb0 ] || native_reject "${1:-}" hardware_unverified
    no_reader aidoku weread bilicomics || native_reject "${1:-}" reader_state_unverified
    [ "$(lipc-get-prop -s "$power" state)" = active ] || native_reject "${1:-}" power_not_active_or_unreadable
    [ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || native_reject "${1:-}" native_foreground_unverified
    case "$(status statusbar)" in *start/running*) ;; *) native_reject "${1:-}" statusbar_unverified;; esac
    managers=$(pidof awesome) && [ -n "$managers" ] || native_reject "${1:-}" window_manager_unverified
''')
    annotated = change(annotated, '''    for manager in $managers; do
        row=$(proc_row "$manager") || exit 1
        set -- $row; case "$3" in R|S) ;; *) exit 1;; esac
        roster="$roster $manager:$2"
    done
    printf '%s\\n' $roster | sort
''', '''    native_scope=${1:-}
    for manager in $managers; do
        row=$(proc_row "$manager") || native_reject "$native_scope" window_manager_unverified
        set -- $row; case "$3" in R|S) ;; *) native_reject "$native_scope" window_manager_unverified;; esac
        roster="$roster $manager:$2"
    done
    printf '%s\\n' $roster | sort || native_reject "$native_scope" window_manager_unverified
''')
    return text[:start]+annotated+text[end:]


def make_device(original, kind):
    require(kind in ('locator', 'measure'), 'bad_device_kind')
    text = original.decode('ascii')
    old_header = '# AIDOKU_STOCK_PLAN '+kind+':'
    require(text.startswith('#!/bin/sh\n'+old_header), 'unexpected_source_stock_header')
    text = change(text, old_header, f'# AIDOKU_GENERATION_STOCK {kind}: {OPERATOR};')
    text = change(text, '[ "$#" = 4 ] || fail arguments', '[ "$#" = 5 ] || fail arguments')
    text = change(text, 'action=$1 label=$2 mode=$3 space=$4\n', f'action=$1 label=$2 mode=$3 space=$4 expected_boot=$5\nkind={kind}\n')
    verbs = 'admit:stock|reserve:stock|run:stock|close:stock|finalize:stock|absent:stock'
    if kind == 'measure':
        verbs += '|readback:stock|confirm-close:stock'
    text = change(text, 'run:stock|close:stock|absent:stock', verbs)
    text = annotate_native_admission(text)
    text = change(text, ' && line "$state/mode" "$mode"; }', ' && line "$state/mode" "$mode" && line "$state/kind" "$kind"; }')
    text = change(text, '    for rel in payload.tsv .aidoku-installation-complete; do\n', '''    regular "$stage/reference/query.elf" || exit 1
    for helper in "$media/aidoku/app/aidoku-native-query" "$pkg/payload/aidoku/app/aidoku-native-query"; do
        regular "$helper" && [ -x "$helper" ] && cmp -s "$helper" "$stage/reference/query.elf" || exit 1
    done
    for rel in payload.tsv .aidoku-installation-complete; do
''')
    text = change(text, '    for f in "$pkg/payload/aidoku/app/aidoku" "$pkg/payload.tsv"',
                  '    for f in "$pkg/payload/aidoku/app/aidoku" "$pkg/payload/aidoku/app/aidoku-native-query" "$pkg/payload.tsv"')
    text = change(text, 'native_before=$(native) && put "$state/native" "$native_before" && cp "$boot" "$state/boot" || fail native_snapshot',
                  'native_before=$(native) && put "$state/native" "$native_before" && same_boot || fail native_snapshot')
    original_tab = 'sh "$stage/input/navigation.sh" library "$label" >/dev/null || fail navigation\n'
    observe_tab = '''stock_view=$(sh "$stage/input/navigation.sh" inspect "$label") &&
    printf '%s\\n' "$stock_view" | grep -Eq '^library [0-9]+ [0-9]+$' || fail already_on_library_required
'''
    # Begin only on the stock Library tab. A locator is literally no-input;
    # a measurement then has no separate tab navigation before its one tap.
    text = change(text, 'sh "$stage/input/navigation.sh" inspect "$label" >/dev/null || fail stock_tabs\n',
                  observe_tab + ('put "$state/input-original" "$stock_view" || fail pointer_baseline\n' if kind == 'measure' else ''))
    text = change(text, original_tab, '')
    text = change(text, 'touch_stock_idle || fail upload_idle_reset\n', '')
    idle_start = text.index('# One-shot activity only, before a stimulus or upload;')
    idle_end = text.index('\n}\n', idle_start)+len('\n}\n')
    text = text[:idle_start]+text[idle_end:]
    if kind == 'measure':
        text = change(text, '    touch_stock_idle || fail slot_idle_reset\n', '')
    text = change(text, 'tar -xzf - -C "$stage" 9<&- || fail stage_upload',
                  'tar -xzf - -C "$stage" || fail stage_upload')
    if kind == 'measure':
        text = change(text, '    if ! sh "$stage/input/dispatch.sh" tap "$label" "$name" >/dev/null; then',
                      '    put "$state/tap-intent" "$name" || { watcher_cleanup; fail click_intent; }\n'
                      '    if ! sh "$stage/input/dispatch.sh" tap "$label" "$name" >/dev/null; then')
        text = change(text, '    tuple=$(await_own 100) || fail app_ownership\n',
                      '    tuple=$(await_own 100) && remember_visit "$out" "$tuple" || fail app_ownership\n')
        text = change(text, '    restore_launcher || fail launcher_restore\n    result=$(printf',
                      '    finish_one_owned_tap || fail click_completion_unverified\n'
                      '    restore_launcher || fail launcher_restore\n    result=$(printf')
        text = change(text, '    rm "$state/active-slot" "$state/current"\n',
                      '    verify_visit_log "$out" || fail visit_log_changed\n'
                      '    rm "$state/active-slot" "$state/current"\n')
        a = text.index('owned_app() (\n')
        b = text.index('await_own() (\n', a)
        part = change(text[a:b], '''    set -- $worker_row
    printf '%s %s %s %s %s\\n' "$gui" "$gui_birth" "$worker" "$2" "$nonce"
''', '''    set -- $worker_row; waiter=$1; worker_birth=$2
    waiter_row=$(proc_row "$waiter") && argv_is "$waiter" sh "$home/launch.sh" --library &&
        require_traced_launcher "/proc/$waiter/mountinfo" || exit 1
    set -- $waiter_row; waiter_birth=$2
    [ "$waiter_birth" -le "$worker_birth" ] || exit 1
    log_identity=$(inode "/proc/$worker/fd/2") && record_visit_log "$out" "$log_identity" &&
        [ "$(proc_row "$worker" | awk '{print $1,$2}')" = "$waiter $worker_birth" ] &&
        [ "$(proc_row "$waiter" | awk '{print $2}')" = "$waiter_birth" ] || exit 1
    printf '%s %s %s %s %s %s %s\\n' "$gui" "$gui_birth" "$worker" "$worker_birth" "$nonce" "$waiter" "$waiter_birth"
''')
        text = text[:a]+part+text[b:]
        a = text.index('    # Export only the existing ten fixed timestamp rows')
        b = text.index('    emit_file trace ', a)
        export = runpy.run_path(str(HERE/'generation_phase_records.py'))['stock_export_awk'](
            runpy.run_path(str(HERE/'generation_probe.py'))).decode('ascii')
        require("'" not in export, 'unexpected_awk_quoting')
        text = text[:a]+f'''    phase_rows=$(printf '%s\\n' "$delta" | awk -v worker="$worker" '{export}') || fail launcher_phase_export
    printf 'STOCK_OBSERVER_BIND %s %s\\n' "$name" "$worker"
    printf '%s\\n' "$phase_rows"
'''+text[b:]
        # A success must retain the bound worker receipt even after the active
        # slot is removed. Only successful evidence does so at the old boundary.
    start = text.index('close_owned() (\n')
    end = text.index('mkdir "$stage" && put "$stage/owner"', start)
    fence = (HERE/'stock_lock_fence.sh').read_text(encoding='ascii')
    protocol = (HERE/'stock_operation.sh').read_text(encoding='ascii')
    measurement = kind == 'measure'
    no_tap = 'absent "$state/active-slot" && absent "$state/tap-intent" || exit 1'
    protocol = (protocol.replace('@TOOLS@', 'flock usleep cmp stat readlink gzip base64 sqlite3'+(' mount umount timeout' if measurement else ''))
                .replace('@CHECK_TIMEOUT@', '''[ -x /bin/true ] || admission_reason timeout_probe_target_missing
    probe=$(timeout -s KILL 2 /bin/true && printf '.') && [ "$probe" = . ] || admission_reason timeout_selftest_failed''' if measurement else ':')
                .replace('@CLOSE_TAP@', 'finish_one_owned_tap || exit 1' if measurement else no_tap)
                .replace('@INSPECT_TAP@', 'inspect_owned_tap || exit 1' if measurement else no_tap)
                .replace('@RESTORE_LAUNCHER@', 'restore_launcher && ' if measurement else '')
                .replace('@RESTORE_LIGHTS@', 'lights restore && ' if measurement else '')
                .replace('@RESTORE_NAVIGATION@', 'restore_owned_navigation || exit 1' if measurement else ':')
                .replace('@INSPECT_NAVIGATION@', 'stock_navigation_exact || exit 1' if measurement else ':')
                .replace('@RESTORE_ACTIONS@', '|readback|confirm-close' if measurement else '')
                .replace('@RESTORE_CASES@', '''readback) readback_owned || fail readback_unverified;;
            confirm-close) close_owned confirm || fail closeout;;''' if measurement else '')
                .replace('@READBACK_FUNCTIONS@', (HERE/'stock_readback.sh').read_text(encoding='ascii') if measurement else '')
                .replace('@READBACK_RETURN@', '''if [ "$policy" = readback ]; then
        readback_result "$already_closed"; exit $?
    fi''' if measurement else ':')
                .replace('@CLOSE_POLICY@', 'repair' if measurement else 'confirm')
                .replace('@RUN_CLOSE_POLICY@', 'repair' if measurement else 'confirm')
                .replace('@STOP_WATCHER@', 'if [ -n "${watcher:-}" ]; then watcher_cleanup; fi' if measurement else ':'))
    tap = '\n'.join((HERE/name).read_text(encoding='ascii') for name in
                    ('stock_tap_recovery.sh', 'stock_navigation_recovery.sh')) if measurement else ''
    require(re.search(r'@[A-Z_]+@', protocol) is None, 'unexpanded_stock_protocol')
    text = text[:start]+fence+'\n'+tap+'\n'+protocol+text[end:]
    require(text.count('mkdir -m 700 "$state"') == 1 and text.count('tar -xzf - -C "$stage"') == 1,
            'unexpected_mutating_path')
    require(text.count('input/dispatch.sh" tap ') == (kind == 'measure') and
            text.count('mount -o bind ') == (kind == 'measure') and text.count('&& umount ') == (kind == 'measure') and
            text.count('\nrun_slot control-01\n') == (kind == 'measure') and
            'touchScreenSaverTimeout' not in text and 'navigation.sh" library ' not in text and
            'navigation.sh" restore ' not in text, 'invalid_operation_scope')
    out = text.encode('ascii')
    require(len(out) < 65536 and out.endswith(b'\n'), 'invalid_script_frame')
    subprocess.run(['sh', '-n'], input=out, check=True)
    return out


def references(base, kind):
    release = private_path('development_release')
    attempt, = private_path('physical_delivery').glob('attempt-*')
    delivery = json.loads(plain(attempt/'result.json', 65536))
    released = json.loads(plain(attempt/'released.json', 65536))
    require(delivery['source'] == SOURCE and delivery['installed'] is True and delivery['outcome'] == 'installed_verified'
            and delivery['recovery_required'] is False and delivery['release'] == released
            and released['state'] == 'released' and all(released[key] is True for key in RELEASE_PROOFS), 'new_delivery_not_closed')
    proof = json.loads(plain(release/'development-verification.json', 65536))
    require(proof['source'] == SOURCE and proof['all_members_match_staging'] and proof['zip_integrity']
            and proof['changed_members_since_previous_development_zip'] == [QUERY], 'wrong_pushed_bundle')
    old = runpy.run_path(str(private_path('stock_reference')))
    found = old['installed_reference'](base, kind)
    # Read the existing formal member digest; do not hash the executable or bundle.
    published = json.loads(plain(release/'aidoku-kindle-development.manifest.json', 65536))
    record, = (row for row in published['files'] if row['path'] == QUERY)
    digest, size = record['sha256'], record['bytes']
    require(re.fullmatch(r'[a-f0-9]{64}', digest) is not None and type(size) is int, 'wrong_query_release_metadata')
    with zipfile.ZipFile(release/'aidoku-kindle-development.zip') as bundle:
        require(bundle.read('aidoku/launch.sh') == found['reference/aidoku/launch.sh'], 'preserved_launcher_changed')
        if kind == 'measure':
            require(bundle.read(APP) == found['reference/program.elf'], 'preserved_gui_changed')
        query = bundle.read(QUERY)
        require(len(query) == size and base['arm_elf'](query) and query == plain(release/'kindle'/QUERY, 65536)
                and b'/lib/ld-linux-armhf.so.3\0' in query, 'wrong_release_query')
    manifest_leaf = 'reference/kpm/payload.tsv'
    rows = found[manifest_leaf].splitlines(keepends=True)
    keys = [row.rstrip(b'\n').split(b'|', 3)[-1] for row in rows]
    require(len(rows) == len(set(keys)) == 39 and keys.count(QUERY.encode()) == 1, 'wrong_kpm_reference_roster')
    replacement = f'{digest}|{size}|755|{QUERY}\n'.encode('ascii')
    manifest = b''.join(replacement if key == QUERY.encode() else row for key, row in zip(keys, rows, strict=True))
    require(manifest != found[manifest_leaf], 'helper_reference_did_not_change')
    found[manifest_leaf] = manifest
    # This digest is the existing KPM completion protocol field for payload.tsv,
    # not an integrity hash of code or of the test transport.
    found['reference/kpm/.aidoku-installation-complete'] = hashlib.sha256(manifest).hexdigest().encode('ascii')+b'\n'
    found['reference/query.elf'] = query
    return found


def artwork(data):
    require(32 < len(data) <= 2_000_000, 'invalid_guard_size')
    magic, left, top, w, h, footer, lines = struct.unpack_from('<8s6I', data)
    require(magic == b'AIDOTAP1' and 48 <= w <= 1000 and 50 <= h <= 1300 and left+w <= 1860
            and 150 <= top and top+h <= 2330 and (footer, lines) == (2380, 90)
            and len(data) == 32+w*h+1860*lines, 'invalid_fresh_artwork_guard')
    return data


def measure_navigation(original):
    # Preserve the qualified parser and native foreground guard, but strip the
    # old tab-changing interface from this reader. Send KILL directly to each
    # blocking program at the existing hard deadline, never to a wrapper shell.
    # The read-only helper and its watchdog do not need the journal descriptor.
    text = original.decode('ascii')
    text = text[:text.index('await_tab() {\n')]
    text = change(text, '[ "$(lipc-get-prop -s com.lab126.powerd state)" = active ]',
                  'power_state=$(timeout -s KILL 3 lipc-get-prop -s com.lab126.powerd state 9<&-) && [ "$power_state" = active ]')
    text = change(text, '[ "$(lipc-get-prop -s com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ]',
                  'active_app=$(timeout -s KILL 3 lipc-get-prop -s com.lab126.appmgrd activeApp 9<&-) && [ "$active_app" = com.lab126.KPPMainApp ]')
    text = change(text, '    case "$(status statusbar)" in',
                  '    bar_status=$(timeout -s KILL 3 status statusbar 9<&-) || return 1\n    case "$bar_status" in')
    text = change(text, 'native && "$kit/lib/ld-linux-armhf.so.3"',
                  'native && timeout -s KILL 13 9<&- "$kit/lib/ld-linux-armhf.so.3"')
    return (text + '[ "$operation" = inspect ] || fail operation\ninspect || fail stock_tabs\n').encode('ascii')


def stage(root, kind, guard=None):
    base = runpy.run_path(str(private_path('base_runner')))
    stock = base['stock_input']()
    old = runpy.run_path(str(private_path('stock_reference')))
    inputs = {name: body for name, body in stock.items() if name not in ('input/artwork.guard','input/dispatch.sh','input/tile-input')}
    inputs.update(references(base, kind))
    inputs['input/framewatch'] = base['read_plain'](old['WATCHER'], 1048576, True)
    require(base['arm_elf'](inputs['input/framewatch']), 'wrong_frame_observer_abi')
    inputs['plan-kind'] = (kind+'\n').encode('ascii')
    if kind == 'measure':
        inputs['input/navigation.sh'] = measure_navigation(stock['input/navigation.sh'])
        subprocess.run(['sh', '-n'], input=inputs['input/navigation.sh'], check=True)
        require(guard is not None and guard.parent == root and guard.name == 'fresh-artwork.guard', 'missing_current_artwork')
        inputs['input/artwork.guard'] = artwork(plain(guard, 2_000_000))
        require(plain(private_path('stock_templates')/'paired-click/validated', 128) == b'passed\n', 'unverified_click_tool')
        inputs['input/tile-input'] = base['read_plain'](private_path('stock_templates')/'paired-click/tile-input', 2_000_000, True)
        require(base['arm_elf'](inputs['input/tile-input']), 'wrong_click_tool_abi')
        dispatch = change(stock['input/dispatch.sh'].decode('ascii'), '[ "$#" = 4 ] && put "$out/pointer-brackets"',
                          '[ "$#" = 2 ] && put "$out/pointer-brackets"').encode('ascii')
        subprocess.run(['sh', '-n'], input=dispatch, check=True)
        inputs['input/dispatch.sh'] = dispatch
        inputs['clock'] = runpy.run_path(str(private_path('staging_tools')))['retained_clock'](base)
        inputs['trace-launch.sh'] = plain(root/'diagnostic-launch.sh', 65536)
    else:
        require(kind == 'locator' and guard is None, 'wrong_locator_plan')
    dest = root/kind
    dest.mkdir(mode=0o700)
    script = plain(root/f'{kind}-template.sh', 65536)
    write(dest/'device.sh', script)
    frame = dest/'payload.framed'
    space = base['package_payload'](inputs, frame, script)
    write(dest/'space-kib', f'{space}\n'.encode('ascii'))
    with frame.open('rb') as handle:
        require(handle.read(len(script)) == script, 'wrong_framed_script')
        remaining = inputs.copy()
        seen = set()
        dirs = {'fixture/imports'} | {p.as_posix() for name in inputs for p in PurePosixPath(name).parents if p != PurePosixPath('.')}
        with tarfile.open(fileobj=handle, mode='r|gz') as archive:
            for member in archive:
                require(member.name not in seen, 'duplicate_payload_member')
                seen.add(member.name)
                if member.isdir():
                    require(member.name in dirs, 'unexpected_payload_directory')
                    continue
                require(member.isreg() and member.name in remaining and member.size == len(remaining[member.name])
                        and archive.extractfile(member).read() == remaining.pop(member.name), 'wrong_payload_member')
        require(not remaining, 'missing_payload_member')
    roster = {name: len(body) for name, body in sorted(inputs.items())}
    write(dest/'roster.json', (json.dumps(roster, indent=2)+'\n').encode('ascii'))
    return {'kind': kind, 'framed_bytes': frame.stat().st_size, 'space_kib': space, 'files': len(roster)}


def locator_receipt(root, attempt):
    require(attempt.parent == root and LABEL.fullmatch(attempt.name) is not None and attempt.resolve(strict=True) == attempt,
            'foreign_locator_attempt')
    intent = json.loads(plain(root/'locator-sent.json', 65536))
    release = json.loads(plain(attempt/'released.json', 65536))
    result = json.loads(plain(attempt/'result.json', 65536))
    require(intent == {'attempt': attempt.name, 'kind': 'locator'} and result['source'] == SOURCE and
            result['diagnostic'] == DIAGNOSTIC and result['operator'] == OPERATOR and result['mode'] == 'locator' and result['complete'] is True and
            result['closeout'] == 'restored' and result['recovery_required'] is False and
            result['release'] == release and release['state'] == 'released' and all(release[k] is True for k in RELEASE_PROOFS),
            'locator_not_closed_in_this_workspace')
    return attempt/'locator.frame'


def seal(root, attempt):
    observed = locator_receipt(root, attempt)
    require(not (root/'measure').exists(), 'measurement_already_sealed')
    # Reuse the existing independent classification and ARM selector check, not
    # any of its retained images. The captured frame remains private on test-env.
    argv = ['/opt/kindle-toolchain/ubuntu-run', str(private_path('classifier')),
            'xvfb-run','-a','-s','-screen 0 1860x2480x24','python3','-B','freeze_target.py','--classify']
    reply = subprocess.run(argv, input=plain(observed, 4613000), capture_output=True, timeout=70)
    require(reply.returncode == 0, 'fresh_stock_card_not_unique')
    guard = root/'fresh-artwork.guard'
    write(guard, artwork(reply.stdout))
    evidence = stage(root, 'measure', guard)
    write(root/'locator-qualified.json', (json.dumps({'qualified': True, 'locator_attempt': attempt.name,
                                                     'source': SOURCE, 'diagnostic': DIAGNOSTIC,
                                                     'operator': OPERATOR})+'\n').encode('ascii'))
    return evidence


def prepare(root):
    lab = json.loads(plain(private_path('generation_lab')/'firmware-qualified.json', 65536))
    require(lab['source'] == SOURCE and lab['diagnostic'] == DIAGNOSTIC and lab['physical_access'] is False
            and {row['profile'] for row in lab['profiles'] if row['guest_stopped'] and row['exact_launcher_restoration_and_fixture_cleanup']}
            == {'scribe', 'pw6'}, 'missing_double_firmware_qualification')
    prepare_templates(root)
    evidence = stage(root, 'locator')
    require(not (root/'measure').exists() and not (root/'fresh-artwork.guard').exists(), 'measurement_requires_live_locator')
    return evidence | {'source': SOURCE, 'diagnostic': DIAGNOSTIC, 'physical_contact': False, 'live_locator_required_for_measure': True}


def prepare_templates(root):
    for kind, leaf in (('locator','locator/device.sh'),('measure','measure-template.sh')):
        write(root/f'{kind}-template.sh', make_device(plain(private_path('stock_templates')/leaf, 65536), kind))
    tools = runpy.run_path(str(HERE/'generation_probe.py'))
    original = plain(private_path('development_release')/'kindle/aidoku/launch.sh', 65536)
    diag = tools['diagnostic_copy'](original)
    require(diag == plain(private_path('generation_lab')/'diagnostic-launch.sh', 65536), 'trace_only_copy_differs_from_firmware_qualification')
    write(root/'diagnostic-launch.sh', diag)


if __name__ == '__main__':
    os.umask(0o077)
    require(HERE.parent == Path('/root') and HERE.name.startswith('aidoku-stock-generation-20261127.')
            and not HERE.stat().st_mode & 0o077, 'wrong_generation_stock_workspace')
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='action', required=True)
    commands.add_parser('prepare')
    seal_cmd = commands.add_parser('seal')
    seal_cmd.add_argument('attempt', type=Path)
    args = cli.parse_args()
    print(json.dumps(prepare(HERE) if args.action == 'prepare' else seal(HERE, args.attempt), indent=2))
