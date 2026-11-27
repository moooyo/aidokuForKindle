#!/usr/bin/env python3
"""Instrument and decode the ordinary launcher's first-generation preparation."""

import argparse
import re
from pathlib import Path


OPT_IN = b'launcher-generation-v1'
OUTER = ('worker_enter', 'observer_start', 'observer_wait_begin', 'observer_ready',
         'portrait_begin', 'portrait_ready', 'display_begin', 'display_ready', 'app_spawn_begin', 'app_spawn_returned')
PARENT = ('observer_fork_returned', 'nonce_written', 'epoch_written', 'generation_cleanup_begin',
          'cover_records_cleared', 'startup_records_cleared', 'generation_written', 'phase_written', 'observer_owned')
CHILD = ('observer_record_write_begin', 'observer_record_rename_pending', 'observer_record_write_returned')
ORDER = (OUTER[0], OUTER[1], *PARENT[:-1], OUTER[2], OUTER[3], PARENT[-1], *OUTER[4:])
BIN_US = 10_000

ADD = b'''GENERATION_DIAG_ENABLED=0
# Private diagnostics use only shell builtins and the existing append-only log.
generation_diag_mark() {
    [ "$GENERATION_DIAG_ENABLED" -eq 1 ] && [ "$LAUNCHER_DIAG_ENABLED" -eq 1 ] || return 0
    { IFS=' ' read -r generation_diag_uptime generation_diag_idle </proc/uptime; } 2>/dev/null || return 0
    printf 'aidoku: generation-diag v1 %s %s %s %s\\n' "$1" "$generation_diag_uptime" "${2:-0}" "${3:-0}" >&2 || :
}
'''
PATCH = (
    (b'LAUNCHER_DIAG_ENABLED=0\nlauncher_diag_mark() {', ADD+b'LAUNCHER_DIAG_ENABLED=0\nlauncher_diag_mark() {'),
    (b'    unset AIDOKU_LAUNCHER_DIAGNOSTICS\n    launcher_diag_mark worker_enter',
     b'    if [ "${AIDOKU_LAUNCHER_DIAGNOSTICS:-}" = '+OPT_IN+b' ]; then\n'
     b'        LAUNCHER_DIAG_ENABLED=1; GENERATION_DIAG_ENABLED=1\n    fi\n'
     b'    unset AIDOKU_LAUNCHER_DIAGNOSTICS\n    launcher_diag_mark worker_enter'),
    (b'    if [ "${1:-}" = --library ]; then AIDOKU_MANAGED_LIBRARY=1; export AIDOKU_MANAGED_LIBRARY; fi\n',
     b'    if [ "${1:-}" = --library ]; then\n'
     b'        AIDOKU_MANAGED_LIBRARY=1; export AIDOKU_MANAGED_LIBRARY\n'
     b'        AIDOKU_LAUNCHER_DIAGNOSTICS='+OPT_IN+b'; export AIDOKU_LAUNCHER_DIAGNOSTICS\n    fi\n'),
    (b'    run_observer & OBSERVER_PID=$!\n', b'    run_observer & OBSERVER_PID=$!\n'
     b'    generation_diag_mark observer_fork_returned "$OBSERVER_PID"\n'),
    (b'    HELPER_COMMAND=$!; write_record observer-command "$HELPER_COMMAND"\n',
     b'    HELPER_COMMAND=$!\n    generation_diag_mark observer_record_write_begin "$HELPER_SELF" "$HELPER_COMMAND"\n'
     b'    if [ "$GENERATION_DIAG_ENABLED" -eq 1 ]; then\n'
     b'        printf \'%s\\n\' "$HELPER_COMMAND" >"$LOCK_DIR/observer-command.pending"\n'
     b'        generation_diag_mark observer_record_rename_pending "$HELPER_SELF" "$HELPER_COMMAND"\n'
     b'        mv "$LOCK_DIR/observer-command.pending" "$LOCK_DIR/observer-command"\n'
     b'    else\n        write_record observer-command "$HELPER_COMMAND"\n    fi\n'
     b'    generation_diag_mark observer_record_write_returned "$HELPER_SELF" "$HELPER_COMMAND"\n'),
    (b'owned_pid "$observer_command" "$OBSERVER_PID"; then launcher_diag_mark observer_ready; return 0; fi',
     b'owned_pid "$observer_command" "$OBSERVER_PID"; then\n'
     b'            launcher_diag_mark observer_ready\n'
     b'            generation_diag_mark observer_owned "$OBSERVER_PID" "$observer_command"\n'
     b'            return 0\n        fi'),
    (b'write_record nonce "$NONCE"; write_record epoch 0\nstart_application 0\n',
     b'write_record nonce "$NONCE"\ngeneration_diag_mark nonce_written\n'
     b'write_record epoch 0\ngeneration_diag_mark epoch_written\nstart_application 0\n'),
    (b'    if [ "$APP_GENERATION" -gt 1 ]; then LAUNCHER_DIAG_ENABLED=0; fi\n    clear_sleep_cover_records\n',
     b'    if [ "$APP_GENERATION" -gt 1 ]; then LAUNCHER_DIAG_ENABLED=0; fi\n'
     b'    generation_diag_mark generation_cleanup_begin\n    clear_sleep_cover_records\n'
     b'    generation_diag_mark cover_records_cleared\n'),
    (b'    rm -f "$LOCK_DIR"/resume-native-portrait-calibration-*.pending\n    write_record generation "$APP_GENERATION:$NONCE"\n',
     b'    rm -f "$LOCK_DIR"/resume-native-portrait-calibration-*.pending\n'
     b'    generation_diag_mark startup_records_cleared\n    write_record generation "$APP_GENERATION:$NONCE"\n'
     b'    generation_diag_mark generation_written\n'),
    (b'    set_phase starting\n    # Every ELF generation',
     b'    set_phase starting\n    generation_diag_mark phase_written\n    # Every ELF generation'),
)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def change(source, old, new):
    require(source.count(old) == 1, 'Unexpected source anchor')
    return source.replace(old, new, 1)


def diagnostic_copy(source):
    require(source.startswith(b'#!/bin/sh\n') and b'GENERATION_DIAG_ENABLED' not in source, 'Unexpected original launcher')
    result = source
    for old, new in PATCH:
        result = change(result, old, new)
    original = result
    for old, new in reversed(PATCH):
        original = change(original, new, old)
    require(original == source, 'Unexpected launcher change')
    return result


CLOCK = rb' ([0-9]{1,14})\.([0-9]{2})'
V1 = re.compile(rb'aidoku: launcher-diag v1 ([a-z_]{1,36})'+CLOCK)
PREP = re.compile(rb'aidoku: generation-diag v1 ([a-z_]{1,36})'+CLOCK+rb' ([0-9]{1,10}) ([0-9]{1,10})')
START = re.compile(rb'aidoku: Launcher worker started \(pid=([1-9][0-9]{0,9})\)\.')
STOP = b'aidoku: Launcher worker exited with status 0.'


def elapsed(start, end):
    return [max(0, end[0] - start[1]), end[1] - start[0]]


def clock_window(row):
    at = (int(row[2])*100 + int(row[3]))*BIN_US
    return at, at+BIN_US


def worker_from_log(raw):
    (match,) = (m for line in raw.splitlines() if (m := START.fullmatch(line)))
    return int(match[1])


def summarize(raw, worker_pid):
    """Call only on the log delta already bound to one closed worker visit."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= 1_048_576 and raw.endswith(b'\n'), 'Invalid worker log')
    require(type(worker_pid) is int and 1 < worker_pid <= 2**31-1, 'Invalid bound worker')
    marks, parent, child, ids = {}, [], [], {}
    start = end = None
    for index, line in enumerate(raw.split(b'\n')):
        if line.startswith(b'aidoku: Launcher worker started'):
            row = START.fullmatch(line)
            require(start is None and row is not None and int(row[1]) == worker_pid, 'Wrong worker')
            start = index
        elif line.startswith(b'aidoku: Launcher worker exited'):
            require(end is None and line == STOP, 'Unsuccessful or repeated worker exit')
            end = index
        elif line.startswith((b'aidoku: launcher-diag', b'aidoku: generation-diag')):
            outer = line.startswith(b'aidoku: launcher-diag')
            row = (V1 if outer else PREP).fullmatch(line)
            require(row is not None, 'Malformed diagnostic row')
            name = row[1].decode('ascii')
            require(name not in marks and name in (OUTER if outer else PARENT+CHILD), 'Unknown or repeated marker')
            marks[name] = (index, clock_window(row))
            (child if name in CHILD else parent).append(name)
            if not outer:
                pair = (int(row[4]), int(row[5]))
                require(all(str(v).encode('ascii') == s and v <= 2**31-1 for v, s in zip(pair, row.groups()[3:], strict=True)),
                        'Invalid observer identity field')
                ids[name] = pair
    require(tuple(parent) == ORDER and tuple(child) == CHILD, 'Incomplete or reordered timing streams')
    require(start is not None and end is not None and marks[OUTER[0]][0] < start < marks[OUTER[1]][0]
            and all(marks[OUTER[1]][0] < marks[key][0] < end for key in (*CHILD, OUTER[-1])),
            'Timing rows outside the same worker log')
    observer, command = ids['observer_owned']
    require(1 < observer and 1 < command and len({worker_pid, observer, command}) == 3
            and ids[PARENT[0]] == (observer, 0) and all(ids[k] == (observer, command) for k in CHILD)
            and all(ids[k] == (0, 0) for k in PARENT[1:-1]), 'Observer command not bound to the accepted record')

    # Within either process the rows have known order. Across processes an
    # observer may publish before the parent returns from the fork or log its
    # write return after parent acceptance. Only the write and acceptance
    # bounds constrain the invisible atomic publication point.
    times = {key: list(window) for key, (_, window) in marks.items()}
    begin, pending, returned = CHILD
    order_edges = (*zip(ORDER[:-1], ORDER[1:], strict=True), ('observer_start', begin),
                   (begin, pending), (pending, returned), (pending, 'observer_ready'))
    require(all(times[a][0] <= times[b][0] for a, b in order_edges), 'Reversed timing stream')
    point = 'observer_record_visible'
    times[point] = [times[pending][0], min(times[returned][1], times['observer_ready'][1])]
    edges = (*order_edges, (pending, point), (point, returned), (point, 'observer_ready'))
    for _ in range(len(times)):
        for first, second in edges:
            a, b = times[first], times[second]
            b[0] = max(b[0], a[0])
            a[1] = min(a[1], b[1])
    require(all(a <= b for a, b in times.values()), 'Inconsistent observer publication or thread clock')

    def span(first, last):
        return elapsed(times[first], times[last])

    start_keys = ('observer_start', *PARENT[:-1], 'observer_wait_begin')
    names = ('observer_fork_return', 'nonce_write', 'epoch_write', 'generation_setup_entry', 'clear_cover_records',
             'clear_startup_records', 'generation_write', 'export_generation_and_publish_phase', 'phase_return_to_wait_entry')
    after_lo = times['observer_wait_begin'][0] - times[point][1]
    after_hi = times['observer_wait_begin'][1] - times[point][0]
    return {'clock_bin_us': BIN_US, 'observer_start_to_wait_begin_us': span('observer_start', 'observer_wait_begin'),
            'observer_wait_begin_to_owned_us': span('observer_wait_begin', 'observer_ready'),
            'observer_start_to_owned_us': span('observer_start', 'observer_ready'),
            'parent_steps_us': {name: span(a, b) for name, a, b in zip(names, start_keys[:-1], start_keys[1:], strict=True)},
            'observer_record_write_us': span(begin, returned), 'observer_record_rename_call_us': span(pending, returned),
            'observer_record_publication_after_start_us': span('observer_start', point),
            'observer_record_publication_to_parent_acceptance_us': span(point, 'observer_ready'),
            'wait_entry_minus_record_publication_us': [after_lo, after_hi],
            'publication_relation_to_wait_entry': ('before' if after_lo > 0 else 'after' if after_hi < 0 else 'unresolved'),
            'verified_matching_observer_command': True, 'measured_service_subscription': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = diagnostic_copy(args.source.read_bytes())
    with args.output.open('xb') as out:
        out.write(data)
