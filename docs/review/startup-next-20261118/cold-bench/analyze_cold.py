#!/usr/bin/env python3
"""Audit cold local-cover A/B evidence on test-env. Never connects to a device."""

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from types import ModuleType


WARM_SHA = 'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'
COLD_RUNNER_SHA = '0d56c4d2ad634a165f6184b12aff9a3fc27dc5f5662293052270f4c910561f26'
COLD_DEVICE_SHA = '49d597c9aae6348244b12e2b9a3345c2e60cb227fbab58ee3b7a3fe07f87c057'
REGIME = 'empty-cache-v1'
POLICY = 'per-case-empty-cache-v1'
MECHANISM = 'cold_local_cover_decode_validation'
START = 'cover_preparation_started'
END = 'cover_preparation_finished'
PREPARED = 'cover_local_derivative_prepared'
REUSED = 'cover_local_derivative_reused'
BEGIN_DECODE = 'cover_cold_validation_combined_started'
VALIDATED = 'cover_cold_decode_validated'
FALLBACK = 'cover_cold_validation_fallback'
TRACKED = frozenset((START, END, PREPARED, REUSED, BEGIN_DECODE, VALIDATED, FALLBACK))


def load_reference():
    # Use a private module instance, including its own globals, for this run.
    # Read a pinned source snapshot once. No on-disk warm file is modified.
    choices = (Path(__file__).resolve().parents[1] / 'tools/analyze_bench.py',
               Path('/root/aidoku-next-bench-20261118/tools/analyze_bench.py'))
    for path in choices:
        if path.is_file():
            source = path.read_bytes()
            if sha256(source).hexdigest() != WARM_SHA:
                raise ValueError('The comparison analyzer does not match the reviewed snapshot')
            module = ModuleType('_indexed_entry_evidence')
            module.__file__ = str(path)
            exec(compile(source, str(path), 'exec'), module.__dict__)
            return module
    raise ValueError('The reviewed comparison analyzer is unavailable')


def fixture(directory, require):
    require((directory / 'cold-fixture').read_bytes() == b'empty-cache-v1\n', 'missing_empty_cache_proof')
    require((directory / 'cold-counts').read_bytes() == b'6 0\n', 'cold_count_sidecar_mismatch')
    seed = (directory / 'seed.sha256').read_bytes()
    require(re.fullmatch(rb'[a-f0-9]{64}  library\.sqlite3\n[a-f0-9]{64}  device-profile\n', seed), 'invalid_seed_manifest')
    fingerprint = sha256(seed).hexdigest()
    require((directory / 'cold-fixture.sha256').read_bytes() == (fingerprint + '\n').encode('ascii'),
            'cold_fixture_manifest_digest_mismatch')
    return fingerprint


def case_events(directory, reference, row):
    check = reference.require
    events = reference.read_json(directory / 'application.json').get('events')
    check(isinstance(events, list) and all(isinstance(e, dict) for e in events), 'malformed_cold_events')
    counts = Counter(e.get('name') for e in events if isinstance(e.get('name'), str) and e['name'] in TRACKED)
    row.update(jobs_started=counts[START], jobs_finished=counts[END], derivatives_generated=counts[PREPARED],
               derivatives_reused=counts[REUSED], combined_attempts=counts[BEGIN_DECODE],
               combined_successes=counts[VALIDATED], explicit_fallbacks=counts[FALLBACK])
    check(counts[START] == counts[END] == counts[PREPARED] == 6 and not counts[REUSED], 'not_six_cold_local_derivatives')
    endpoint = [e for e in events if e.get('name') == 'startup_visible_covers_settled']
    check(len(endpoint) == 1 and endpoint[0].get('cover_pending') is False
          and endpoint[0].get('cover_images') == endpoint[0].get('cover_slots') == 7
          and reference.natural(endpoint[0].get('boottime_us')), 'cold_visible_covers_incomplete')
    cutoff = endpoint[0]['boottime_us']
    unique_count = reference.read_json(directory / 'state.json').get('cover_visible_count')
    check(unique_count == 6, 'not_six_visible_cold_items')
    jobs = []
    active = None
    for e in events:
        name = e.get('name')
        if not isinstance(name, str) or name not in TRACKED:
            continue
        t = e.get('boottime_us')
        check(reference.natural(t) and t <= cutoff, 'cold_job_outside_startup')
        if name == START:
            check(active is None, 'overlapping_cold_jobs')
            active = {START: t}
            continue
        check(active is not None and name not in active and t >= max(active.values()), 'cold_event_order_mismatch')
        active[name] = t
        if name == END:
            check(PREPARED in active, 'missing_derivative_for_cold_job')
            check((BEGIN_DECODE in active) == (VALIDATED in active), 'cold_validation_outcome_unmatched')
            job = {'ordinal': len(jobs) + 1, 'duration_ms': reference.ms(t - active[START]),
                   'used_combined_validation': VALIDATED in active, 'explicit_fallback': FALLBACK in active}
            if VALIDATED in active:
                check(active[BEGIN_DECODE] <= active[VALIDATED] <= active[PREPARED] and FALLBACK not in active,
                      'malformed_combined_validation_sequence')
                job['combined_decode_ms'] = reference.ms(active[VALIDATED] - active[BEGIN_DECODE])
            jobs.append(job)
            active = None
    check(active is None and len(jobs) == 6, 'incomplete_cold_job_sequence')
    if directory.name.startswith('baseline-'):
        check(not any(counts[n] for n in (BEGIN_DECODE, VALIDATED, FALLBACK)), 'control_exercised_cold_candidate')
    row['jobs'] = jobs
    row['jobs_without_combined_validation'] = 6 - counts[VALIDATED]
    if directory.name.endswith('-qualify'):
        lines = (directory / 'shell.tsv').read_text('ascii').splitlines()
        stamps = [s for line in lines if (s := line.split()) and s[-1] == 'native_capture_started']
        check(len(stamps) == 1 and len(stamps[0]) == 2 and re.fullmatch(r'\d+\.\d{1,6}', stamps[0][0]),
              'missing_cold_capture_clock')
        start = int(Decimal(stamps[0][0]) * 1_000_000)
        check(start >= cutoff + 10_000, 'cold_capture_preceded_complete_covers')
        row['native_capture_after_complete_covers_ms'] = reference.ms(start - cutoff)


def mechanism(batches):
    all_rows = [r for b in batches for r in b['cases']]
    roles = {}
    for role, selection in [('timing', lambda n: n.split('-')[-1].isdigit()),
                            ('pixel', lambda n: n.endswith('-qualify')),
                            ('warmup', lambda n: n.endswith('-warmup'))]:
        group = [r for r in all_rows if selection(r['case'])]
        by_side = {}
        for side in ('baseline', 'candidate'):
            data = [r.get('cold', {}) for r in group if r['case'].startswith(side + '-')]
            by_side[side] = {'observations': len(data), 'exercised_runs': sum(r.get('combined_successes', 0) > 0 for r in data),
                             'combined_successes': sum(r.get('combined_successes', 0) for r in data),
                             'explicit_fallbacks': sum(r.get('explicit_fallbacks', 0) for r in data)}
        roles[role] = by_side
    active = [roles[k] for k in ('timing', 'pixel') if roles[k]['candidate']['observations']]
    verified = bool(active and all(bool(g['baseline']['observations']) and g['candidate']['combined_successes'] > 0
                                   and g['baseline']['combined_successes'] == 0 for g in active))
    return dict(name=MECHANISM, verified=verified, **roles)


def analyze(paths, qualification, phase, crop):
    reference = load_reference()
    audit_case = reference.audit_case
    audit_batch = reference.audit_batch
    seeds = set()

    def cold_case(output, name, commands):
        record = audit_case(output, name, commands)
        directory = output / name
        data = record['cold'] = {'fixture_verified': False, 'trace_verified': False}
        try:
            data_seed = fixture(directory, reference.require)
            seeds.add(data_seed)
            data['fixture_verified'] = True
        except (OSError, UnicodeError):
            record['issues'].append('missing_cold_fixture_files')
        except reference.Invalid as error:
            record['issues'].append(str(error))
        try:
            case_events(directory, reference, data)
            data['trace_verified'] = True
        except (OSError, UnicodeError):
            record['issues'].append('missing_cold_trace_file')
        except reference.Invalid as error:
            record['issues'].append(str(error))
        if record['issues'] and record['status'] == 'ok':
            record['status'] = 'failed'
        return record

    def cold_batch(root, mode, primary):
        result = audit_batch(root, mode, primary)
        plan = reference.read_json(root / 'plan.json')
        for obj in (plan, reference.read_json(root / 'report.json')):
            if obj.get('cache_regime') != REGIME or obj.get('fixture_policy') != POLICY:
                result['issues'].append('unexpected_cold_fixture_policy')
        if result['runner_sha256'] != COLD_RUNNER_SHA or result['device_script_sha256'] != COLD_DEVICE_SHA:
            result['issues'].append('unrecognized_cold_fixture_runner')
        result['fixture_policy'] = POLICY
        result['cache_regime'] = REGIME
        result['issues'] = sorted(set(result['issues']))
        return result

    reference.audit_case = cold_case
    reference.audit_batch = cold_batch
    output = reference.analyze(paths, qualification, phase, 'covers', crop)
    match = len(seeds) == 1
    all_rows = [r for b in output['batches'] for r in b['cases']]
    verified = bool(all_rows and all(r['cold']['fixture_verified'] and r['cold']['trace_verified'] for r in all_rows))
    output.update(cache_regime=REGIME, fixture_policy=POLICY, cold_fixture_match=match,
                  cold_fixture_verified=verified, warm_analyzer_sha256=WARM_SHA,
                  analyzer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  mechanism=mechanism(output['batches']))
    if not match or not verified:
        output['issues'].append('cold_fixture_or_trace_unverified')
        output.update(valid=False, latency_gate_passed=None, qualification_passed=False)
    if not output['valid']:
        output['mechanism']['verified'] = False
    output['scope_note'] = ('Local offline source images, a new empty per-case application derivative cache and a shared seed. '
                            'OS page caches are not flushed. Zero accelerated jobs and fallbacks remain in latency estimates. '
                            'User images, metadata, source paths and seed hashes are never exported. '
                            'Independent lifecycle validation and release approval are separate.')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=Path, action='append', required=True)
    parser.add_argument('--qualification-batch', type=Path, action='append', default=[])
    parser.add_argument('--phase', choices=('pilot', 'screen', 'confirm', 'qualify'), required=True)
    parser.add_argument('--primary', choices=('covers',), default='covers')
    parser.add_argument('--status-bar-pixels', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Run only on test-env')
    result = analyze(args.batch, args.qualification_batch, args.phase, args.status_bar_pixels)
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: result[k] for k in ('phase', 'complete_pairs', 'valid', 'cold_fixture_match',
                                             'cold_fixture_verified', 'latency_gate_passed', 'qualification_passed', 'issues')}))
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    sys.exit(main())
