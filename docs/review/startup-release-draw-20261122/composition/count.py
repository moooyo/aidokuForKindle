"""Read-only fill census, scoped to the true first release Gray8 Library rendering call."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


MODELS = {'scribe': (1860, 2480, 72, 160), 'pw6': (1264, 1680, 56, 144)}


def inspect(raw, shape):
    width, height, status, navigation = shape
    assert (raw['width'], raw['height'], raw['paper']) == (width, height, 255)
    assert raw['transitions'] == 2 and raw['overflow'] == 0
    count = height - status - navigation
    screen = raw['whole_runs'] == 1 and [0, height] in raw['runs']
    match = screen and raw['runs'].count([status, count]) == 1
    return {'mapped_full_scene': screen, 'mapped_library_page': match, 'expected_body_rows': count,
            'observed_second_paper_bytes': count * width if match else None,
            'observed_full_damage_fraction': count / height if match else None, 'raw_counter': raw}


def census(binary, counter, output):
    if output.exists():
        raise ValueError('Never re-enter a census')
    records = {}
    result = {'scope': 'Release stock application, native retained Gray8 path on x86; fill counts only, no timing', 'profiles': records}
    with output.open('x') as stream:
        try:
            for profile, shape in MODELS.items():
                width, height, _, _ = shape
                with tempfile.TemporaryDirectory() as directory:
                    case = Path(directory)
                    env = dict(os.environ, TZ='UTC', AIDOKU_ATTACH_TRIAL_CASE=str(case),
                               AIDOKU_ATTACH_TRIAL_ARM='control', AIDOKU_ATTACH_TRIAL_CHECK='normal',
                               AIDOKU_RELEASE_DRAW_PROFILE='1', LD_PRELOAD=str(counter),
                               AIDOKU_FILL_SIZE=f'{width}x{height}', AIDOKU_FILL_PAPER='255',
                               AIDOKU_FILL_RECEIPT=str(case / 'counter.json'))
                    for name in ('AIDOKU_PRELINE_ARM', 'AIDOKU_PRELINE_CJK'):
                        env.pop(name, None)
                    proc = subprocess.run([binary, '--device', profile, '--data-dir', case / 'data', '--offline',
                                           '--exit-after-ms', '100', '--performance-trace', case / 'trace.json'],
                                          env=env, capture_output=True, timeout=20)
                    if proc.returncode:
                        raise ValueError(proc.stderr.decode(errors='replace')[-1000:])
                    trace = json.loads((case / 'trace.json').read_bytes())
                    assert trace['success'] and trace['finished'] and trace['dropped_events'] == 0
                    events = [event['name'] for event in trace['events']]
                    assert all(events.count(name) == 1 for name in ('attach_trial_first_library_draw', 'attach_trial_interaction_verified'))
                    assert (case / 'library.gray').stat().st_size == width * height
                    records[profile] = inspect(json.loads((case / 'counter.json').read_bytes()), shape)
            result['status'] = 'complete'
        except BaseException:
            result['status'] = 'stopped'
            raise
        finally:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('counter', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    census(args.binary.resolve(strict=True), args.counter.resolve(strict=True), args.output)
