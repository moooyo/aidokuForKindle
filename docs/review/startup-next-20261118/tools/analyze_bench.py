#!/usr/bin/env python3
"""Analyze predeclared indexed-entry batches on test-env without device access."""

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
import sys


SIDES = ('baseline', 'candidate')
FLAGS = ('success', 'lease_released', 'same_boot', 'native_restored', 'entry_restored',
         'installed_and_user_unchanged', 'input_roster_unchanged')
MARKS = ('native_open_requested', 'launcher_bridge', 'launcher_entry', 'launcher_worker',
         'observer_start', 'observer_ready', 'portrait_start', 'portrait_done',
         'display_takeover_start', 'display_takeover_done', 'application_exec_requested',
         'native_restored', 'native_home_active')
EVENTS = {'ready': 'startup_ready', 'first': 'startup_library_frame_submitted',
          'input_ready': 'startup_library_input_ready', 'covers': 'startup_visible_covers_settled'}
ENDPOINTS = (*EVENTS, 'usable', 'exit')
CASE = re.compile(r'(baseline|candidate)-(\d{2}|warmup|qualify|diag[1-9]\d*)\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


class Invalid(Exception):
    """Message must be a fixed public code, not input data or a private path."""


def require(value, reason):
    if not value:
        raise Invalid(reason)


def read_json(path):
    try:
        obj = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        raise Invalid('invalid_or_missing_json') from None
    require(isinstance(obj, dict), 'invalid_json_object')
    return obj


def natural(value):
    return type(value) is int and value >= 0


def marks(path):
    found = {}
    try:
        for line in path.read_text('ascii').splitlines():
            stamp, name = line.split()
            if name in MARKS:
                require(name not in found and re.fullmatch(r'\d+\.\d{1,6}', stamp), 'invalid_shell_mark')
                found[name] = int(Decimal(stamp) * 1_000_000)
    except (OSError, ValueError, UnicodeError):
        raise Invalid('invalid_or_missing_shell_marks') from None
    require(all(k in found for k in MARKS), 'missing_shell_mark')
    require(all(found[a] <= found[b] for a, b in zip(MARKS, MARKS[1:])), 'unordered_shell_marks')
    return found


def ms(microseconds):
    return round(microseconds / 1000, 3)


def delta(left, right):
    return round(left - right, 3)


def trace_case(directory, primary):
    if primary:
        require(not any((directory / n).exists() for n in ('final.png', 'native-settled.ppm', 'system.csv')),
                'primary_has_diagnostic_capture')
    shell = marks(directory / 'shell.tsv')
    trace = read_json(directory / 'application.json')
    require(trace.get('success') is True and trace.get('finished') is True and trace.get('dropped_events') == 0
            and trace.get('clock') == 'monotonic_rust_main', 'incomplete_or_unrecognized_trace')
    events = trace.get('events')
    require(isinstance(events, list) and all(isinstance(e, dict) and isinstance(e.get('name'), str) for e in events),
            'invalid_events')
    selected = {}
    for key, name in dict(EVENTS, shutdown='shutdown_started').items():
        hits = [e for e in events if e['name'] == name]
        if not hits and key == 'covers':
            raise Invalid('cover_endpoint_not_reached')
        require(len(hits) == 1 and natural(hits[0].get('boottime_us')), 'missing_or_duplicate_endpoint')
        selected[key] = hits[0]
    clock = {k: e['boottime_us'] for k, e in selected.items()}
    origin = shell['native_open_requested']
    require(origin <= clock['ready'] <= clock['first'] <= clock['input_ready'] <= clock['shutdown']
            and clock['first'] <= clock['covers'] <= clock['shutdown'] <= shell['native_home_active'],
            'inconsistent_endpoint_clocks')
    first, settled = selected['first'], selected['covers']
    require(all(natural(selected[k].get('marker')) and selected[k]['marker'] > 0
                for k in ('first', 'input_ready', 'covers')), 'unaccepted_frame')
    slots = settled.get('cover_slots')
    require(natural(slots) and slots > 0 and first.get('cover_slots') == slots and settled.get('cover_pending') is False
            and settled.get('cover_images') == slots and natural(first.get('cover_images'))
            and first['cover_images'] <= slots, 'partial_or_changed_cover_projection')
    state = read_json(directory / 'state.json')
    require(state.get('route') == 'library' and state.get('cover_visible_failure_count') == 0
            and natural(state.get('cover_visible_count')) and 0 < state['cover_visible_count'] <= slots,
            'unexpected_final_library')
    durations = {k: ms(clock[k] - origin) for k in EVENTS}
    durations['usable'] = max(durations['first'], durations['input_ready'])
    durations['exit'] = ms(shell['native_home_active'] - clock['shutdown'])
    counts = Counter(e['name'] for e in events if natural(e.get('boottime_us')) and e['boottime_us'] <= clock['covers'])
    try:
        engine = (directory / 'portrait-engine').read_bytes()
    except OSError:
        engine = b''
    diag = {'request_to_launcher_bridge_ms': ms(shell['launcher_bridge'] - origin),
            'worker_entry_ms': ms(shell['launcher_worker'] - shell['launcher_entry']),
            'observer_ready_ms': ms(shell['observer_ready'] - shell['observer_start']),
            'portrait_outer_ms': ms(shell['portrait_done'] - shell['portrait_start']),
            'takeover_ms': ms(shell['display_takeover_done'] - shell['display_takeover_start']),
            'cover_tail_ms': ms(clock['covers'] - clock['first']), 'cover_slots': slots,
            'unique_visible_items': state['cover_visible_count'], 'first_frame_images': first['cover_images'],
            'reported_local_cache_hits': counts['cover_local_derivative_reused'],
            'reported_local_cache_misses': counts['cover_local_derivative_prepared'],
            'reported_cover_jobs': counts['cover_preparation_finished'],
            'portrait_engine': {b'native\n': 'native', b'shell\n': 'shell', b'unmarked\n': 'unmarked'}.get(engine, 'unknown'),
            'warm_attempts': counts['startup_cover_warm_attempt'], 'warm_hits': counts['startup_cover_warm_hit'],
            'cover_memory_hits': counts['cover_local_derivative_memory_hit'], 'warm_stops': counts['startup_cover_warm_stopped'],
            'library_frames_through_covers': counts['startup_cover_frame_submitted']}
    for k in ('first', 'covers'):
        cost = selected[k].get('frame_cost_us')
        require(isinstance(cost, list) and len(cost) == 4 and all(natural(v) for v in cost), 'invalid_frame_costs')
        diag[k + '_frame_cost_ms'] = [ms(v) for v in cost]
    return {'endpoints_ms': durations, 'diagnostics': diag}


def check_operating_evidence(directory, diag):
    try:
        log = (directory / 'launcher.log').read_bytes().splitlines()
        identities = [(directory / ('catalog-' + p + '.identity')).read_bytes() for p in ('before', 'after')]
        times = [(directory / ('catalog-' + p + '.access')).read_bytes().strip() for p in ('before', 'after')]
    except OSError:
        raise Invalid('missing_operating_evidence') from None
    require(log.count(b'aidoku: Catalog notification completed.') == 1
            and not any(b'Catalog notification unavailable;' in line for line in log), 'catalog_notification_mismatch')
    require(identities[0] == identities[1] and len(identities[0].splitlines()) == 1 and identities[0].strip(),
            'catalog_identity_changed')
    require(all(re.fullmatch(rb'[0-9]{1,12}', stamp) for stamp in times)
            and int(times[1]) > int(times[0]), 'recent_access_did_not_advance')
    prefix = b'aidoku: Startup portrait supervisor: '
    engines = [line[len(prefix):] for line in log if line.startswith(prefix)]
    expected = {(): 'unmarked', (b'shell.',): 'shell', (b'native.',): 'native'}.get(tuple(engines))
    require(expected is not None and diag['portrait_engine'] == expected, 'portrait_engine_evidence_mismatch')


def audit_case(output, name, commands):
    directory = output / name
    record = {'case': name, 'status': 'ok', 'runner_gate_verified': False, 'issues': []}
    cmd = [c for c in commands if isinstance(c, dict) and c.get('name') == 'case-' + name]
    if any(c.get('timeout') is True or c.get('returncode') == 124 for c in cmd):
        record['issues'].append('case_command_deadline')
    if any(c.get('returncode') != 0 for c in cmd):
        record['issues'].append('case_command_failed')
    if not directory.is_dir():
        record['issues'].append('missing_case_directory')
    else:
        try:
            record['runner_gate_verified'] = (directory / 'case-ok').read_bytes() == b'ok\n'
        except OSError:
            pass
        if not record['runner_gate_verified']:
            record['issues'].append('case_gate_missing_or_failed')
        try:
            record.update(trace_case(directory, name.split('-')[-1].isdigit()))
            check_operating_evidence(directory, record['diagnostics'])
            record['operating_evidence_verified'] = True
        except Invalid as error:
            record['issues'].append(str(error))
    reasons = record['issues']
    if 'case_command_deadline' in reasons:
        record['status'] = 'deadline'
    elif 'missing_case_directory' in reasons:
        record['status'] = 'missing'
    elif 'cover_endpoint_not_reached' in reasons:
        record['status'] = 'censored'
    elif reasons:
        record['status'] = 'failed'
    return record


def identities(report):
    variants = report.get('variants')
    require(isinstance(variants, dict) and set(variants) == set(SIDES), 'invalid_variant_set')
    out = {}
    for side in SIDES:
        row = variants[side]
        keys = ('variant_sha256', 'application_sha256', 'launcher_sha256', 'native_query_sha256')
        require(isinstance(row, dict) and all(isinstance(row.get(k), str) and HASH.fullmatch(row[k]) for k in keys),
                'missing_variant_identity')
        out[side] = {k: row[k] for k in keys}
        extra = row.get('extra_sha256', {})
        require(isinstance(extra, dict) and all(isinstance(k, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', k)
                and isinstance(v, str) and HASH.fullmatch(v) for k, v in extra.items()), 'invalid_extra_identity')
        out[side]['extra_sha256'] = extra
        raw = {k: row[k] for k in keys[1:]}
        require(isinstance(row.get('args'), list) and isinstance(row.get('env'), dict), 'missing_variant_configuration')
        raw.update(args=row['args'], env=row['env'], extra_sha256=extra)
        require(row['variant_sha256'] == sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                'variant_configuration_digest_mismatch')
    return out


def audit_batch(root, mode, primary_metric):
    report = read_json(root / 'report.json')
    frozen = read_json(root / 'plan.json')
    run = report.get('run_id')
    require(isinstance(run, str) and re.fullmatch(r'[a-z0-9-]{1,64}', run), 'invalid_run_id')
    require(report.get('mode') == mode, 'batch_phase_mismatch')
    names = report.get('cases')
    require(isinstance(names, list) and names and all(isinstance(n, str) and CASE.fullmatch(n) for n in names)
            and len(names) == len(set(names)), 'invalid_case_plan')
    output = root / 'outputs/out'
    issues = ['batch_guard_' + key for key in FLAGS if report.get(key) is not True]
    bind = ('run_id', 'mode', 'primary', 'cases', 'variants', 'device_script_sha256', 'runner_sha256', 'payload_sha256')
    if any(k not in frozen or report.get(k) != frozen[k] for k in bind) or frozen.get('primary') != primary_metric:
        issues.append('frozen_plan_mismatch')
    for filename, key in [('device.sh', 'device_script_sha256'), ('runner.py', 'runner_sha256')]:
        try:
            require(sha256((root / filename).read_bytes()).hexdigest() == frozen.get(key), 'frozen_runner_mismatch')
        except (Invalid, OSError):
            issues.append('frozen_runner_mismatch')
    try:
        before, after = ((root / filename).read_bytes() for filename in ('before.stdout', 'after.stdout'))
        if not before or before != after:
            issues.append('protected_snapshot_changed')
    except OSError:
        issues.append('missing_protected_snapshots')
    try:
        proof = read_json(root / 'lease-released.json')
        if proof.get('run_id') != run or not all(proof.get(k) is True for k in (
                'ok', 'owner_and_token_checked', 'native_restored_and_connections_closed', 'lease_directory_absent_after_release')):
            issues.append('invalid_release_proof')
    except Invalid:
        issues.append('missing_release_proof')
    if output.is_dir() and any(d.is_dir() and CASE.fullmatch(d.name) and d.name not in names for d in output.iterdir()):
        issues.append('unplanned_case_directory')
    commands = report.get('commands', [])
    require(isinstance(commands, list), 'invalid_command_log')
    rows = [audit_case(output, name, commands) for name in names]
    if any(r['status'] != 'ok' for r in rows):
        issues.append('planned_cases_incomplete')
    primary = [r for r in rows if r['case'].split('-')[-1].isdigit()]
    paired = []
    if len(primary) % 2:
        issues.append('unpaired_case_in_plan')
    for left, right in zip(primary[::2], primary[1::2]):
        lside, lnum = left['case'].split('-'); rside, rnum = right['case'].split('-')
        if lnum != rnum or lside == rside:
            issues.append('unpaired_case_in_plan')
            continue
        pair = {'batch': run, 'round': int(lnum), 'order': 'AB' if lside == 'baseline' else 'BA',
                'complete': left['status'] == right['status'] == 'ok'}
        if pair['complete']:
            a, b = (left, right) if lside == 'baseline' else (right, left)
            pair.update(baseline_ms=a['endpoints_ms'], candidate_ms=b['endpoints_ms'])
            pair['savings_ms'] = {k: delta(a['endpoints_ms'][k], b['endpoints_ms'][k]) for k in ENDPOINTS}
        paired.append(pair)
    return {'run_id': run, 'mode': mode, 'artifacts': identities(report),
            'frozen_plan_sha256': sha256((root / 'plan.json').read_bytes()).hexdigest(),
            'runner_sha256': frozen.get('runner_sha256') if HASH.fullmatch(str(frozen.get('runner_sha256'))) else None,
            'device_script_sha256': frozen.get('device_script_sha256') if HASH.fullmatch(str(frozen.get('device_script_sha256'))) else None,
            'batch_guards': {k: report.get(k) is True for k in FLAGS},
            'issues': sorted(set(issues)), 'cases': rows, 'pairs': paired}


def pixels(root, explicit_crop):
    from PIL import Image, ImageChops

    pictures, crops = [], []
    for side in SIDES:
        directory = root / 'outputs/out' / (side + '-qualify')
        try:
            with Image.open(directory / 'native-settled.ppm') as source:
                require(source.format == 'PPM' and source.width * source.height <= 12_000_000, 'invalid_native_capture')
                pictures.append(source.convert('RGB'))
        except (OSError, ValueError, Image.DecompressionBombError):
            raise Invalid('invalid_native_capture') from None
        size = pictures[-1].size
        if (directory / 'final.metadata.json').is_file():
            meta = read_json(directory / 'final.metadata.json')
            profile = meta.get('profile')
            require(profile in ('scribe', 'pw5') and meta.get('route') == 'library' and meta.get('backend') == 'kindle'
                    and meta.get('landscape') is False and meta.get('rotation_degrees') == 0
                    and meta.get('window_size') == list(size), 'capture_metadata_mismatch')
        else:
            require(size == (1860, 2480), 'capture_metadata_missing_for_unknown_geometry')
            profile = 'scribe'
        width, height, bar = (930, 1240, 36) if profile == 'scribe' else (618, 824, 28)
        require(size[0] * height == size[1] * width and size[1] * bar % height == 0, 'capture_geometry_mismatch')
        crops.append(size[1] * bar // height)
    require(pictures[0].size == pictures[1].size and crops[0] == crops[1], 'capture_pair_geometry_mismatch')
    top = crops[0]
    require(explicit_crop is None or explicit_crop == top, 'status_bar_crop_mismatch')
    width, height = pictures[0].size
    area = (0, top, width, height)
    # Keep framebuffer pixels, bounding boxes and per-user screenshot hashes private.
    diff = ImageChops.difference(pictures[0].crop(area), pictures[1].crop(area))
    return {'width': width, 'height': height, 'status_bar_excluded_pixels': top,
            'identical_below_status_bar': diff.getbbox() is None}


def stats(values):
    return {'n': len(values), 'median': round(median(values), 3), 'min': min(values), 'max': max(values)} if values else None


def mechanism(batches, primary):
    rows = [r for b in batches for r in b['cases'] if r['status'] == 'ok' and r['case'].split('-')[-1].isdigit()]
    groups = {side: [r['diagnostics'] for r in rows if r['case'].startswith(side + '-')] for side in SIDES}
    sides = {s: {'observations': len(v), 'portrait_engines': dict(Counter(r['portrait_engine'] for r in v)),
                 'runs_with_warm_attempts': sum(r['warm_attempts'] > 0 for r in v),
                 'runs_with_warm_hits': sum(r['warm_hits'] > 0 for r in v),
                 'runs_with_cover_memory_hits': sum(r['cover_memory_hits'] > 0 for r in v),
                 'zero_eligible_or_no_warm_hit_runs': sum(r['warm_hits'] == 0 for r in v)} for s, v in groups.items()}
    control, candidate = groups['baseline'], groups['candidate']
    if primary == 'first':
        verified = bool(control and candidate and all(r['portrait_engine'] in ('unmarked', 'shell') for r in control)
                        and all(r['portrait_engine'] == 'native' for r in candidate))
    else:
        # Keep every zero-hit launch in the paired estimates. Do not certify a
        # feature that was never observed warming and consuming a cache entry.
        verified = bool(control and candidate and all(r['warm_attempts'] == r['warm_hits'] == 0 for r in control)
                        and any(r['warm_attempts'] > 0 and r['warm_hits'] > 0 and r['cover_memory_hits'] > 0
                                for r in candidate))
    return {'name': 'startup_portrait_supervisor' if primary == 'first' else 'bounded_cached_covers',
            'verified': verified, **sides}


def analyze(paths, qualification, phase, primary, crop):
    batches, qualifying, checks, issues, errors = [], [], [], [], []
    run_ids = set()
    inputs = [(phase, p) for p in paths] + [('qualify', p) for p in qualification]
    for ordinal, (kind, directory) in enumerate(inputs, 1):
        try:
            batch = audit_batch(directory, kind, primary)
            require(batch['run_id'] not in run_ids, 'duplicate_run_id')
            run_ids.add(batch['run_id'])
            (qualifying if kind == 'qualify' else batches).append(batch)
            issues.extend(batch['issues'])
            names = {r['case'] for r in batch['cases']}
            if kind == 'qualify':
                require(all(s + '-qualify' in names for s in SIDES), 'missing_planned_qualification')
                check = pixels(directory, crop)
                checks.append(dict(batch=batch['run_id'], **check))
                if not check['identical_below_status_bar']:
                    issues.append('native_pixels_differ')
            elif kind in ('screen', 'confirm') and any(n.endswith('-qualify') for n in names):
                issues.append('qualification_not_independent')
        except Invalid as error:
            errors.append({'input_ordinal': ordinal, 'reason': str(error)})
    all_batches = batches + qualifying
    if len({json.dumps(b['artifacts'], sort_keys=True) for b in all_batches}) > 1:
        issues.append('variant_identity_changes_across_batches')
    if len(batches) > 1:
        # Confirm halves may be distinct lease windows. Require the same private
        # tested-device boot, app/catalog inputs, user fixture, input roster and
        # native policy/light snapshot; export only the equality decision.
        try:
            snapshots = [(path / 'before.stdout').read_bytes() for path in paths]
            if len(set(snapshots)) != 1:
                issues.append('different_initial_state_across_comparison_batches')
        except OSError:
            issues.append('missing_cross_batch_initial_state')
        if len({(b['runner_sha256'], b['device_script_sha256']) for b in batches}) != 1:
            issues.append('different_measurement_tools_across_comparison_batches')
    pairs = [p for b in batches for p in b['pairs']]
    completed = [p for p in pairs if p['complete']]
    rowset = [r for b in batches for r in b['cases'] if r['case'].split('-')[-1].isdigit()]
    shapes = {(r['diagnostics']['cover_slots'], r['diagnostics']['unique_visible_items']) for b in all_batches
              for r in b['cases'] if r['status'] == 'ok' and (r['case'].split('-')[-1].isdigit() or r['case'].endswith('-qualify'))}
    if len(shapes) > 1:
        issues.append('visible_fixture_changed')
    comparisons = {}
    orders = Counter(p['order'] for p in pairs)
    for k in ENDPOINTS:
        values = [p['savings_ms'][k] for p in completed]
        comparisons[k] = {s: stats([p[s + '_ms'][k] for p in completed]) for s in SIDES}
        comparisons[k].update(paired_savings_ms=stats(values), improved_pairs=sum(v > 0 for v in values),
                              order_savings_ms={order: stats([p['savings_ms'][k] for p in completed if p['order'] == order])
                                                for order in ('AB', 'BA')})
    counts = Counter(r['status'] for r in rowset)
    expected, wins = (6, 5) if phase == 'screen' else (10, 9) if phase == 'confirm' else (None, None)
    if expected and ([p['round'] for p in pairs] != list(range(1, expected + 1)) or len(completed) != expected
                     or orders['AB'] != expected // 2 or orders['BA'] != expected // 2):
        issues.append('planned_pair_count_or_order_incomplete')
    main, guard = ('usable', 'covers') if primary == 'first' else ('covers', 'usable')
    result_valid = bool(all_batches) and not errors and not issues
    decision = None
    if expected and result_valid:
        decision = (comparisons[main]['paired_savings_ms']['median'] >= 100 and comparisons[main]['improved_pairs'] >= wins
                    and comparisons[guard]['paired_savings_ms']['median'] >= -50)
    exercised = mechanism(batches, primary)
    return {
        'schema_version': 1, 'phase': phase, 'primary_endpoint': primary, 'primary_metric': main,
        'method': 'A=baseline, B=candidate. Paired savings = baseline minus candidate. Milliseconds from native request to software/driver event; usable is the later of first frame and software input readiness. Exit starts at application shutdown. No optical timing. Warmups and independent captures are excluded. No optional omission of failed planned pairs.',
        'analyzer_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'artifacts': all_batches[0]['artifacts'] if all_batches else {},
        'batches': all_batches, 'input_failures': errors, 'issues': sorted(set(issues)), 'pairs': pairs,
        'planned_primary_cases': len(rowset), 'primary_case_counts': {n: counts[n] for n in ('ok', 'missing', 'failed', 'deadline', 'censored')},
        'required_pairs': expected, 'planned_pairs': len(pairs), 'complete_pairs': len(completed),
        'pair_orders': dict(orders), 'comparisons_ms': comparisons, 'pixels': checks,
        'valid': result_valid, 'latency_gate_passed': decision, 'mechanism': exercised,
        'gate': None if not expected else {'minimum_paired_median_saving_ms': 100, 'minimum_improved_pairs': wins,
                                           'maximum_other_paired_median_regression_ms': 50},
        'qualification_passed': bool(checks) and result_valid,
        'scope_note': 'Pilot is descriptive only. Screening requires independent confirmation. A latency win alone cannot authorize deployment; inspect mechanism, order, exit, pixels, independent lifecycle and installed-entry evidence.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, action='append', required=True, help='Private batch directories in predeclared order')
    parser.add_argument('--qualification-batch', type=Path, action='append', default=[])
    parser.add_argument('--primary', choices=('first', 'covers'), required=True)
    parser.add_argument('--phase', choices=('screen', 'confirm', 'pilot', 'qualify'), required=True)
    parser.add_argument('--status-bar-pixels', type=int, help='Must match the verified capture geometry')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run this read-only analysis on test-env')
    out = analyze(args.batch, args.qualification_batch, args.phase, args.primary, args.status_bar_pixels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: out[k] for k in ('phase', 'primary_endpoint', 'complete_pairs', 'valid', 'latency_gate_passed',
                                         'qualification_passed', 'issues', 'input_failures')}))
    return 0 if out['valid'] else 2


if __name__ == '__main__':
    sys.exit(main())
