"""Summarize twelve observed warm startup timings without private inputs."""

import argparse
import json
import statistics
from pathlib import Path


SOURCE_RUN = 'aidoku-next-covers-screen-db500309'


def analyze_case(directory):
    raw = json.loads((directory / 'application.json').read_text())
    assert raw['success'] and not raw['dropped_events']
    events = raw['events']

    def select(name):
        return [event for event in events if event['name'] == name]

    anchor, = select('startup_library_frame_submitted')
    first = anchor['boottime_us']

    def relative(event):
        return (event['boottime_us'] - first) / 1000

    def times(name):
        return [relative(event) for event in select(name)]

    begin = times('cover_preparation_started')
    hit = times('cover_local_derivative_reused')
    end = times('cover_preparation_finished')
    admit, = times('startup_background_covers_admitted')
    final_send, = times('startup_visible_covers_settled')
    assert len(begin) == len(hit) == len(end) == 6
    assert all(a <= b <= c for a, b, c in zip(begin, hit, end))
    assert all(a < b for a, b in zip(end, begin[1:]))
    frames = select('startup_cover_frame_submitted')
    assert len(frames) in (2, 3) and frames[-1]['cover_images'] == frames[-1]['cover_slots'] == 7
    assert all(event['marker'] is not None for event in frames)
    # The uninstrumented deadline is assigned between these two recorded events.
    lower, upper = admit + 300, 300.0
    assert not any(lower <= t <= upper for t in [*begin, *end, *hit])
    done_count = sum(t < lower for t in end)
    active = next((i + 1 for i, (start, stop) in enumerate(zip(begin, end))
                   if start < lower and stop > upper), None)
    part = next((event for event in frames[1:-1] if event['cover_pending']), None)
    predicted = bool(part and part['cover_images'] == 6 and active == 6 and done_count == 5)
    return {
        'case': directory.name,
        'deadline_after_first_frame_ms': [round(lower, 3), upper],
        'completed_jobs_at_deadline': done_count,
        'cached_hit_markers_at_deadline': sum(t < lower for t in hit),
        'active_job_ordinal_at_deadline': active,
        'sixth_job_start_ms': begin[-1],
        'sixth_job_local_hit_ms': hit[-1],
        'sixth_job_finished_ms': end[-1],
        'submitted_slots_per_frame': [event['cover_images'] for event in frames],
        'partial_frame_send_ms': relative(part) if part else None,
        'partial_render_start_latest_ms': round(relative(part) - sum(part['frame_cost_us']) / 1000, 3) if part else None,
        'complete_frame_send_ms': final_send,
        'complete_frame_previous_buffer_wait_ms': frames[-1]['frame_cost_us'][1] / 1000,
        'last_job_active_in_six_slot_partial_case': predicted,
        'fits_35ms_on_observed_timeline': predicted and end[-1] <= lower + 35,
        'fits_40ms_on_observed_timeline': predicted and end[-1] <= lower + 40,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('private_runs', type=Path, help='Parent of the completed private run')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run = args.private_runs / SOURCE_RUN
    result = json.loads((run / 'report.json').read_text())
    assert result['success'] and result['lease_released'] and result['cases_completed']
    folder = run / 'outputs/out'
    rows = [analyze_case(folder / f'{side}-{i:02}') for i in range(1, 7) for side in ('baseline', 'candidate')]
    partial = [row for row in rows if row['partial_frame_send_ms'] is not None]
    simple = [row for row in rows if row['partial_frame_send_ms'] is None]
    target = [row for row in partial if row['last_job_active_in_six_slot_partial_case']]
    out = {'run_id': SOURCE_RUN, 'cases': rows, 'summary': {
        'cases': len(rows), 'partial_frames': len(partial), 'six_slot_partial_with_final_job_active': len(target),
        'already_reported_final_cache_hit_at_ordinary_deadline_in_target_cases': sum(row['cached_hit_markers_at_deadline'] == 6 for row in target),
        'observed_jobs_inside_absolute_335ms_window_in_target_cases': sum(row['fits_35ms_on_observed_timeline'] for row in rows),
        'observed_jobs_inside_absolute_340ms_window_in_target_cases': sum(row['fits_40ms_on_observed_timeline'] for row in rows),
        'median_tail_with_partial_ms': round(statistics.median(row['complete_frame_send_ms'] for row in partial), 3),
        'median_tail_without_partial_ms': round(statistics.median(row['complete_frame_send_ms'] for row in simple), 3),
    }, 'limits': [
        'Time zero is the first accepted Library frame. The actual deadline is set between background admission and the first Library observation; clock brackets span less than 0.4 ms.',
        'A partial frame is marked after SEND. The exact publication decision and UI acceptance of a worker result are not instrumented. Subtracting the four measured nonoverlapping backend durations from SEND gives only the latest possible render start.',
        'Until the local hit marker, a worker is in its cache/source validation and image preparation path. These traces cannot isolate hashing versus lookup versus PNG decoding, or show a hit proof before that marker.',
        'Conditional timing differences compare distinct launches, not a causal intervention. A grace period changes scheduling. Neither eligibility nor an observed completion inside a hypothetical window predicts a specific production saving.',
    ]}
    args.output.write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out['summary']))


if __name__ == '__main__':
    main()
