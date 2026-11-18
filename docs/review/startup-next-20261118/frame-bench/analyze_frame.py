#!/usr/bin/env python3
"""Evaluate one bounded last-cover grace opportunity from existing test-env records."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import ModuleType


BASE_ANALYZER = 'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'
BASE_RUNNER = '85d60c15b871375477cd14ef2020f0cc6adb7119ef618a010bec6ed934d97f7c'
BASE_DEVICE = '3126a4c5ceffb3836218f9f9ccb9dab2b111c67c56a0d380c98a8167379da14d'
APP = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
REGIME = 'warm-local-derivatives-v1'
POLICY = 'six-local-covers-warm-v1'
MECHANISM = 'startup_final_cover_grace'
PREFIX = 'startup_cover_last_grace_'
STATES = ('started', 'completed', 'expired', 'cancelled')
CADENCE_MS = 300
GRACE_MS = 40


def load_reference():
    for path in (Path(__file__).resolve().parents[1] / 'tools/analyze_bench.py',
                 Path('/root/aidoku-next-bench-20261118/tools/analyze_bench.py')):
        if path.is_file():
            source = path.read_bytes()
            if sha256(source).hexdigest() != BASE_ANALYZER:
                raise ValueError('The comparison analyzer differs from the frozen source')
            mod = ModuleType('_indexed_entry_frame_evidence')
            mod.__file__ = str(path)
            exec(compile(source, str(path), 'exec'), mod.__dict__)
            return mod
    raise ValueError('Frozen indexed-entry analyzer unavailable')


def audit_events(directory, ref, role='primary'):
    check = ref.require
    events = ref.read_json(directory / 'application.json').get('events')
    check(isinstance(events, list) and all(isinstance(e, dict) and isinstance(e.get('name'), str) and
                                          ref.natural(e.get('at_us')) for e in events), 'invalid_frame_trial_events')
    check(all(a['at_us'] <= b['at_us'] for a, b in zip(events, events[1:])), 'nonmonotonic_application_clock')

    def select(name):
        return [e for e in events if e['name'] == name]

    def once(name):
        hits = select(name)
        check(len(hits) == 1, 'missing_or_duplicate_frame_trial_event')
        return hits[0]

    admission = once('startup_background_covers_admitted')['at_us']
    first, settled = (once(name) for name in ('startup_library_frame_submitted', 'startup_visible_covers_settled'))
    ready = once('startup_ready')['at_us']
    start, end = first['at_us'], settled['at_us']
    check(first.get('cover_slots') == settled.get('cover_slots') == settled.get('cover_images') == 7
          and settled.get('cover_pending') is False and ref.read_json(directory / 'state.json').get('cover_visible_count') == 6,
          'not_six_visible_warm_items')
    if role == 'pixel':
        # The frozen capture helper starts its six-second delay only after
        # receiving the application's ready log. No such sleep enters timings.
        check(end - ready < 5_980_000, 'warm_native_pixels_not_proven_settled')
    finishes = [e['at_us'] for e in select('cover_preparation_finished')]
    starts = [e['at_us'] for e in select('cover_preparation_started')]
    check(len(starts) == len(finishes) == 6 and all(start <= a <= b <= end for a, b in zip(starts, finishes))
          and all(a <= b for a, b in zip(finishes, starts[1:])), 'six_serial_visible_jobs_not_observed')
    hits = select('cover_local_derivative_reused')
    misses = select('cover_local_derivative_prepared')
    check(len(hits) + len(misses) == 6 and (role == 'warmup' or (len(hits), len(misses)) == (6, 0)),
          'derivative_regime_mismatch')
    check(all(sum(a <= e['at_us'] <= b for e in hits + misses) == 1 for a, b in zip(starts, finishes)),
          'unmatched_local_cover_result')
    check(not any(e['name'].startswith(('startup_cover_warm_', 'cover_cold_validation_'))
                  or e['name'] == 'cover_cold_decode_validated' for e in events), 'other_cover_candidate_exercised')
    frames = select('startup_cover_frame_submitted')
    check(frames and frames[0].get('frame_sequence') == first.get('frame_sequence')
          and frames[-1].get('frame_sequence') == settled.get('frame_sequence')
          and frames[0]['at_us'] >= start and frames[-1]['at_us'] <= end,
          'frame_sequence_boundary_mismatch')
    check(all(ref.natural(e.get('frame_sequence')) and e.get('cover_slots') == 7 and
              ref.natural(e.get('cover_images')) and 0 <= e['cover_images'] <= 7 for e in frames)
          and all(a['frame_sequence'] < b['frame_sequence'] for a, b in zip(frames, frames[1:])),
          'invalid_cover_frame_sequence')
    check(frames[-1].get('cover_pending') is False and frames[-1]['cover_images'] == 7,
          'missing_final_seven_slot_frame')
    middle = frames[1:-1]
    check(all(e.get('cover_pending') is True for e in middle), 'unexpected_settled_intermediate_frame')
    notifications = {s: [e['at_us'] for e in select(PREFIX + s)] for s in STATES}
    check(all(len(t) <= 1 for t in notifications.values()) and not
          any(e['name'].startswith(PREFIX) and e['name'][len(PREFIX):] not in STATES for e in events),
          'unexpected_repeated_or_unknown_grace_marker')
    counts = {s: len(t) for s, t in notifications.items()}
    check(counts['started'] == sum(counts[s] for s in STATES[1:]), 'unmatched_grace_decision')
    check(not directory.name.startswith('baseline-') or not counts['started'], 'control_exercised_candidate')
    publications = select('startup_final_cover_publication')
    check(len(publications) <= 1, 'repeated_final_cover_publication')
    result = dict(counts, partial_frames=len(middle), library_frames=len(frames), eligible_jobs_finished=len(finishes),
                  local_cache_hits=len(hits), local_cache_misses=len(misses), actual_grace_wait_ms=None,
                  completed_without_intermediate_frame=False, extra_prior_buffer_settle_ms=ref.ms(settled['frame_cost_us'][1]),
                  final_job_after_admission_ms=ref.ms(finishes[-1] - admission),
                  frames=[{'after_first_ms': ref.ms(e['at_us'] - start), 'images': e['cover_images'],
                           'pending': e.get('cover_pending')} for e in frames])
    if counts['started']:
        t0 = notifications['started'][0]
        disposition = next(s for s in STATES[1:] if counts[s])
        t1 = notifications[disposition][0]
        check(start <= t0 <= t1 <= end and finishes[-2] <= t0,
              'grace_does_not_cover_last_visible_job')
        # Finish marks come from the background worker, not UI acceptance. A
        # finished worker result may still await its event-loop turn at t0.
        within = [e for e in middle if t0 <= e['at_us'] < t1]
        if disposition == 'completed':
            check(finishes[-1] <= t1 and len(publications) == 1
                  and t1 <= publications[0]['at_us'] <= frames[-1]['at_us'] and not within,
                  'completion_cannot_be_tied_to_final_publication')
        result.update(actual_grace_wait_ms=ref.ms(t1 - t0), decision=disposition,
                      start_after_admission_ms=ref.ms(t0 - admission), decision_after_admission_ms=ref.ms(t1 - admission),
                      last_job_finished_after_start_ms=ref.ms(finishes[-1] - t0),
                      complete_send_after_decision_ms=ref.ms(end - t1),
                      partial_frames_after_grace_start=sum(e['at_us'] >= t0 for e in middle),
                      partial_frames_while_waiting=len(within),
                      completed_without_intermediate_frame=disposition == 'completed' and
                      not any(e['at_us'] >= t0 for e in middle))
    return result


def mechanism(batches, ref):
    all_rows = [r for b in batches for r in b['cases']]
    output = {}
    for role, eligible in [('timing', lambda n: n.split('-')[-1].isdigit()),
                           ('pixel', lambda n: n.endswith('-qualify')), ('warmup', lambda n: n.endswith('-warmup'))]:
        summary = {}
        for side in ref.SIDES:
            rows = [r['frame_grace'] for r in all_rows if r['case'].startswith(side + '-') and eligible(r['case'])
                    and 'frame_grace' in r]
            observed = [r['actual_grace_wait_ms'] for r in rows if r.get('actual_grace_wait_ms') is not None]
            summary[side] = dict(observations=len(rows), zero_triggers=sum(r['started'] == 0 for r in rows),
                                 starts=sum(r['started'] for r in rows), completions=sum(r['completed'] for r in rows),
                                 expirations=sum(r['expired'] for r in rows), cancellations=sum(r['cancelled'] for r in rows),
                                 partial_frames=sum(r['partial_frames'] for r in rows),
                                 runs_with_partial_frames=sum(r['partial_frames'] > 0 for r in rows),
                                 observed_wait_ms=ref.stats(observed),
                                 completed_without_intermediate_frame=sum(r['completed_without_intermediate_frame'] for r in rows))
        output[role] = summary
    target = output['timing'] if output['timing']['candidate']['observations'] else output['pixel']
    exercised = bool(target['baseline']['observations'] and not target['baseline']['starts']
                     and target['candidate']['completed_without_intermediate_frame'] > 0)
    return dict(name=MECHANISM, verified=exercised, grace_cap_ms=GRACE_MS, cadence_ms=CADENCE_MS,
                calendar='The source deadline is the prior cadence deadline plus the grace cap. Recorded durations use the application monotonic clock and include event-loop scheduling.',
                cancellation_scope='A cancelled marker records a cancellation but cannot identify its cause. User/other-dirty bypass requires separate host lifecycle tests.',
                claim='A candidate completion without a partial frame demonstrates the path executed. Whether it saved time is established only by the entire paired comparison.', **output)


def analyze(paths, qualification, phase, crop):
    ref = load_reference()
    audit_batch, audit_case = ref.audit_batch, ref.audit_case

    def frame_case(output, name, commands):
        record = audit_case(output, name, commands)
        try:
            role = 'warmup' if name.endswith('-warmup') else 'pixel' if name.endswith('-qualify') else 'primary'
            record['frame_grace'] = audit_events(output / name, ref, role)
        except ref.Invalid as error:
            record['issues'].append(str(error))
            if record['status'] == 'ok':
                record['status'] = 'failed'
        return record

    def frame_batch(path, mode, primary):
        b = audit_batch(path, mode, primary)
        plan = ref.read_json(path / 'plan.json')
        if ('cache_regime' in plan or 'fixture_policy' in plan or b['runner_sha256'] != BASE_RUNNER
                or b['device_script_sha256'] != BASE_DEVICE):
            b['issues'].append('not_the_frozen_warm_harness')
        for side, v in plan['variants'].items():
            if (v['launcher_sha256'] != LAUNCH or v['native_query_sha256'] != QUERY or
                    v.get('extra_sha256') != {} or v.get('args') != [] or v.get('env') != {} or
                    (v['application_sha256'] == APP) != (side == 'baseline')):
                b['issues'].append('trial_does_not_isolate_application_default')
        b.update(cache_regime=REGIME, fixture_policy=POLICY, mechanism=mechanism([b], ref))
        b['issues'] = sorted(set(b['issues']))
        if b['issues']:
            b['mechanism']['verified'] = False
        return b

    ref.audit_case, ref.audit_batch = frame_case, frame_batch
    result = ref.analyze(paths, qualification, phase, 'covers', crop)
    result.update(cache_regime=REGIME, fixture_policy=POLICY, analyzer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  warm_analyzer_sha256=BASE_ANALYZER, mechanism=mechanism(result['batches'], ref))
    # Show changes for each actual pair; never turn a zero-trigger case into
    # an exclusion or mistake the difference for a causal per-frame speedup.
    frame_pairs = []
    for b in result['batches']:
        for p in b['pairs']:
            rows = {r['case']: r for r in b['cases']}
            if p['complete']:
                n = p['round']
                a, c = (rows[f'{s}-{n:02d}']['frame_grace'] for s in ref.SIDES)
                frame_pairs.append({'run_id': b['run_id'], 'round': n, 'order': p['order'],
                                    'baseline_partial_frames': a['partial_frames'], 'candidate_partial_frames': c['partial_frames'],
                                    'partial_frame_difference': a['partial_frames'] - c['partial_frames'],
                                    'candidate_triggered': bool(c['started']), 'candidate_decision': c.get('decision'),
                                    'candidate_actual_wait_ms': c.get('actual_grace_wait_ms'),
                                    'usable_saving_ms': p['savings_ms']['usable'], 'covers_saving_ms': p['savings_ms']['covers']})
            else:
                frame_pairs.append({'run_id': b['run_id'], 'round': p['round'], 'order': p['order'], 'incomplete': True})
    result['frame_pairs'] = frame_pairs
    if not result['valid']:
        result['mechanism']['verified'] = False
    result['scope_note'] = ('Local offline warm derivative trials on the same formal Library route. Warmups may populate the derivative cache and do not enter paired estimates. '
                            'Observed wall time between grace markers includes scheduling. Absent intermediate frames are not proof that a frame was saved in a counterfactual launch. '
                            'Inspect independent pixel/exit/lifecycle qualification and never infer a tail percentile from six pairs.')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, action='append', required=True)
    p.add_argument('--qualification-batch', type=Path, action='append', default=[])
    p.add_argument('--primary', choices=('covers',), default='covers')
    p.add_argument('--phase', choices=('pilot', 'screen', 'confirm', 'qualify'), required=True)
    p.add_argument('--status-bar-pixels', type=int)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if sys.platform != 'linux':
        p.error('Run on test-env')
    result = analyze(args.batch, args.qualification_batch, args.phase, args.status_bar_pixels)
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k:result[k] for k in ('phase','complete_pairs','valid','latency_gate_passed','qualification_passed','issues')}))
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    sys.exit(main())
