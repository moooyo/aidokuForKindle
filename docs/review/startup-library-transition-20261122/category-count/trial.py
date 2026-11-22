"""Compare actual first complete Library draws in balanced, fresh software sessions."""

import argparse
import filecmp
import json
import os
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory


PROFILES = {'scribe': (930, 1240), 'pw6': (632, 840)}
ARMS = ('control', 'without_empty_text')
METRICS = ('render_us', 'entry_to_library_us')


def sample(binary, case, profile):
    case.mkdir()
    environment = os.environ.copy()
    environment.pop('AIDOKU_FIRST_DRAW_GPROFNG', None)
    environment.update(AIDOKU_ATTACH_TRIAL_CASE=str(case), AIDOKU_ATTACH_TRIAL_ARM='control',
                       AIDOKU_ATTACH_TRIAL_CHECK='normal', TZ='UTC')
    trace = case / 'trace.json'
    completed = subprocess.run([binary, '--device', profile, '--data-dir', case / 'data', '--offline',
                                '--exit-after-ms', '100', '--performance-trace', trace],
                               env=environment, timeout=20, capture_output=True)
    if completed.returncode:
        raise ValueError(completed.stderr.decode(errors='replace')[-1700:])
    log = json.loads(trace.read_bytes())
    assert log['enabled'] and log['finished'] and log['success'] and log['dropped_events'] == 0
    def stamp(name):
        record, = [event for event in log['events'] if event['name'] == name]
        return record['at_us']
    entry, splash, ready, library, interacted = [stamp(name) for name in (
        'process_entry', 'attach_trial_first_full_draw', 'startup_ready',
        'attach_trial_first_library_draw', 'attach_trial_interaction_verified')]
    width, height = PROFILES[profile]
    assert (case / 'library.rgb').stat().st_size == width * height * 3
    render_ns = int((case / 'first-render-ns').read_text())
    assert entry < splash < ready < library < interacted and 0 < render_ns / 1000 <= library - entry
    return {'render_us': render_ns / 1000, 'entry_to_library_us': library - entry}


def summary(pairs):
    result = {}
    for metric in METRICS:
        left, right = ([row[arm][metric] for row in pairs] for arm in ARMS)
        saved = [a - b for a, b in zip(left, right)]
        result[metric] = {'control_median_us': median(left), 'candidate_median_us': median(right),
                          'paired_median_saving_us': median(saved), 'faster_pairs': sum(value > 0 for value in saved),
                          'control_first_median_saving_us': median(saved[::2]),
                          'candidate_first_median_saving_us': median(saved[1::2])}
    return result


def compare(programs, output):
    if output.exists():
        raise ValueError('Never overwrite or resume an attempted cohort')
    records = {profile: {'visits': []} for profile in PROFILES}
    report = {'schema': 1, 'scope': 'test-env x86_64 dev software renderer; no physical card, framebuffer or panel',
              'schedule': 'one excluded warmup pair, six measured alternating pairs per profile',
              'clock': 'monotonic wall time; first complete renderer.render excludes buffer and framebuffer I/O',
              'cpu_affinity': sorted(os.sched_getaffinity(0)), 'profiles': records}
    with output.open('x') as publication:
        try:
            for pair in range(7):
                for profile in (tuple(PROFILES) if pair % 2 else tuple(reversed(PROFILES))):
                    order = ARMS if pair % 2 else ARMS[::-1]
                    row = {'pair': pair, 'order': list(order)}
                    records[profile]['visits'].append(row)
                    with TemporaryDirectory(prefix='aidoku-empty-text-') as directory:
                        scratch = Path(directory)
                        for arm in order:
                            row[arm] = sample(programs / arm, scratch / arm, profile)
                        row['frame_pixels_equal'] = filecmp.cmp(scratch / ARMS[0] / 'library.rgb',
                                                                scratch / ARMS[1] / 'library.rgb', shallow=False)
                        assert row['frame_pixels_equal'], 'Full first Library pixels differ'
            for record in records.values():
                record['summary'] = summary(record['visits'][1:])
            report['status'] = 'complete'
        except BaseException as error:
            report['status'] = 'stopped'
            report['reason'] = str(error)[:300]
            raise
        finally:
            json.dump(report, publication, indent=2, allow_nan=False)
            publication.write('\n')


def check(path):
    report = json.loads(path.read_bytes())
    assert report['status'] == 'complete' and len(report['cpu_affinity']) == 1 and set(report['profiles']) == set(PROFILES)
    for record in report['profiles'].values():
        assert len(record['visits']) == 7 and record['summary'] == summary(record['visits'][1:])
        for pair, row in enumerate(record['visits']):
            assert pair == row['pair'] and row['order'] == list(ARMS if pair % 2 else ARMS[::-1]) and row['frame_pixels_equal']
            for arm in ARMS:
                assert 0 < row[arm]['render_us'] <= row[arm]['entry_to_library_us']
    print(json.dumps({profile: record['summary'] for profile, record in report['profiles'].items()}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    runner = sub.add_parser('run')
    runner.add_argument('programs', type=Path)
    runner.add_argument('output', type=Path)
    checker = sub.add_parser('check')
    checker.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.action == 'run':
        compare(args.programs.resolve(strict=True), args.output)
    else:
        check(args.output)
