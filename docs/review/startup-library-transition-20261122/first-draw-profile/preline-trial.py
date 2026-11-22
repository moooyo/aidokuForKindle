"""Test a frame-scoped, pre-line-break handoff in the synthetic first-draw fixture."""

import argparse
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import tempfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def visit(binary, folder, profile, arm, cjk):
    folder.mkdir()
    env = dict(os.environ, TZ='UTC', AIDOKU_ATTACH_TRIAL_CASE=str(folder), AIDOKU_ATTACH_TRIAL_ARM='control',
               AIDOKU_ATTACH_TRIAL_CHECK='normal', AIDOKU_PRELINE_ARM=arm)
    env.pop('AIDOKU_PRELINE_CJK', None)
    if cjk:
        env['AIDOKU_PRELINE_CJK'] = '1'
    trace = folder / 'timing.json'
    completed = subprocess.run([binary, '--offline', '--device', profile, '--data-dir', folder / 'data',
                                '--exit-after-ms', '100', '--performance-trace', trace], env=env, capture_output=True, timeout=30)
    require(completed.returncode == 0, 'Fixture did not verify its cover slots or its reversible pointer control')
    record = json.loads(trace.read_bytes())
    require(record['finished'] and record['success'] and not record['dropped_events'], 'Incomplete application trace')
    def single(name):
        value, = [event['at_us'] for event in record['events'] if event['name'] == name]
        return value
    entry, initial, ready, scene, interaction = (single(name) for name in ('process_entry', 'attach_trial_first_full_draw',
        'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified'))
    require(entry < initial < ready < scene < interaction, 'Unexpected frame chronology')
    draw_us = int((folder / 'draw-ns').read_text()) / 1000
    counts = json.loads((folder / 'preline-counts').read_bytes())
    require(len(counts) == 6 and all(type(value) is int and value >= 0 for value in counts), 'Invalid counters')
    require(0 < draw_us <= scene - entry and counts[0] > 0 and counts[1] > 0, 'Missing initial draw')
    size = (930, 1240) if profile == 'scribe' else (632, 840)
    image = (folder / 'library.rgb').read_bytes()
    require(len(image) == size[0] * size[1] * 3, 'Wrong RGB geometry')
    return {'draw_us': draw_us, 'entry_us': scene - entry, 'measure_builds': counts[0], 'paint_builds': counts[1],
            'hits': counts[2], 'different_width_hits': counts[3], 'negative_key_checks': counts[4], 'cjk_hits': counts[5]}, image


def pair(binary, profile, order, cjk=False):
    with tempfile.TemporaryDirectory() as scratch:
        data = {}; reference = None
        for arm in order:
            data[arm], image = visit(binary, Path(scratch) / arm, profile, arm, cjk)
            if reference is not None:
                require(image == reference, 'Control and candidate differ in actual Library pixels')
            reference = image
    baseline, candidate = data['control'], data['reuse']
    require(not baseline['hits'] and baseline['measure_builds'] == candidate['measure_builds'] and candidate['hits'] > 0
            and candidate['paint_builds'] + candidate['hits'] == baseline['paint_builds'], 'No verified reduction in paint shaping')
    require(candidate['different_width_hits'] > 0, 'The trial did not relayout for changed width')
    if cjk:
        require(candidate['negative_key_checks'] == 4 * candidate['hits'] and candidate['cjk_hits'] > 0,
                'Missing CJK or mismatch checks')
    return dict(order=order, **data, equal_pixels=True, reversible_interaction=True)


def summary(pairs):
    data = {}
    for metric in ('draw_us', 'entry_us'):
        differences = [row['control'][metric] - row['reuse'][metric] for row in pairs]
        data[metric] = {'paired_median_saved': median(differences), 'wins': sum(value > 0 for value in differences),
                        'control_median': median(row['control'][metric] for row in pairs),
                        'reuse_median': median(row['reuse'][metric] for row in pairs)}
    data['passes'] = (data['draw_us']['paired_median_saved'] >= 1000 and data['draw_us']['wins'] >= 5
                      and data['entry_us']['paired_median_saved'] >= -1000)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), 'Output must be new')
    binary = args.binary.resolve(strict=True)
    results = {'scope': 'Private x86_64 software renderer with synthetic data; no physical measurements',
               'screen': 'Both profiles need >=1000 us median draw saving, >=5/6 draw wins, <=1000 us median entry regression',
               'profiles': {}}
    for profile in ('scribe', 'pw6'):
        pair(binary, profile, ['control', 'reuse'])
        timed = [pair(binary, profile, ['control', 'reuse'] if index % 2 == 0 else ['reuse', 'control']) for index in range(6)]
        quality = pair(binary, profile, ['control', 'reuse'], cjk=True)
        results['profiles'][profile] = {'excluded_warmup_pairs': 1, 'timed': timed, 'summary': summary(timed), 'separate_cjk': quality}
    results['passes'] = all(record['summary']['passes'] for record in results['profiles'].values())
    with args.output.open('x') as output:
        json.dump(results, output, indent=2); output.write('\n')


if __name__ == '__main__':
    main()
