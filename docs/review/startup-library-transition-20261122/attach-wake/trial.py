"""Balanced real Aidoku controller/software-render trials; no Kindle or disk snapshots."""

import argparse
import filecmp
import json
import os
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory


ARMS = ('control', 'direct')
PROFILES = ('scribe', 'pw6')
INTERVALS = {'attach_to_route': ('application_attached', 'startup_route_started'),
             'attach_to_view': ('application_attached', 'startup_view_started'),
             'initial_to_library': ('attach_trial_first_full_draw', 'attach_trial_first_library_draw'),
             'entry_to_library': ('process_entry', 'attach_trial_first_library_draw')}


def sample(binary, folder, profile, arm, check='normal'):
    folder.mkdir()
    child = subprocess.run([str(binary), '--device', profile, '--data-dir', str(folder / 'data'),
                            '--offline', '--exit-after-ms', '100', '--performance-trace', str(folder / 'trace.json')],
                           env={**os.environ, 'AIDOKU_ATTACH_TRIAL_CASE': str(folder), 'AIDOKU_ATTACH_TRIAL_ARM': arm,
                                'AIDOKU_ATTACH_TRIAL_CHECK': check}, capture_output=True, timeout=15)
    if child.returncode:
        raise ValueError('Visit failed: ' + child.stderr[-1800:].decode(errors='replace'))
    report = json.loads((folder / 'trace.json').read_text())
    assert report['enabled'] and report['finished'] and report['success'] and report['dropped_events'] == 0
    by_name = {}
    for event in report['events']:
        by_name.setdefault(event['name'], []).append(event['at_us'])

    def stamp(name):
        assert len(by_name[name]) == 1, name
        return by_name[name][0]

    assert stamp('attach_trial_first_full_draw') < stamp('application_attached')
    if check == 'stop':
        assert all(key not in by_name for key in ('startup_route_started', 'attach_trial_first_library_draw'))
        assert not (folder / 'library.rgb').exists()
        return {'stop_before_route': True}
    if check == 'suspend':
        assert stamp('application_attached') < stamp('attach_trial_suspended_dispatch') < stamp('startup_route_started')
    else:
        assert 'attach_trial_suspended_dispatch' not in by_name
    chain = [stamp(key) for key in ('application_attached', 'startup_route_started', 'startup_view_started',
                                    'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified')]
    assert chain == sorted(chain)
    size = (930, 1240) if profile == 'scribe' else (632, 840)
    assert (folder / 'library.rgb').stat().st_size == size[0] * size[1] * 3
    return {name: stamp(end) - stamp(start) for name, (start, end) in INTERVALS.items()}


def summarize(pairs):
    summary = {}
    for name in INTERVALS:
        values = [row['control'][name] - row['direct'][name] for row in pairs]
        summary[name] = {'control_us': median(row['control'][name] for row in pairs),
                         'direct_us': median(row['direct'][name] for row in pairs),
                         'paired_median_us': median(values), 'direct_earlier': sum(value > 0 for value in values),
                         'AB_median_us': median(values[::2]), 'BA_median_us': median(values[1::2]),
                         'range_us': [min(values), max(values)]}
    return summary


def run(binary, output):
    if output.exists():
        raise ValueError('Refusing to overwrite results')
    report = {'unit': 'microseconds', 'scope': 'test-env x86_64, real asynchronous bootstrap, Slint software draw return; not native SEND',
              'profiles': {}, 'lifecycle': []}
    for profile in PROFILES:
        for check in ('stop', 'suspend'):
            for arm in ARMS:
                with TemporaryDirectory(prefix='aidoku-attach-check-') as temp:
                    sample(binary, Path(temp) / arm, profile, arm, check)
                    report['lifecycle'].append([profile, arm, check])
        pairs = []
        for pair in range(9):
            order = ARMS if pair % 2 else ARMS[::-1]
            row = {'pair': pair, 'order': list(order)}
            with TemporaryDirectory(prefix='aidoku-attach-ui-') as temp:
                root = Path(temp)
                for arm in order:
                    row[arm] = sample(binary, root / arm, profile, arm)
                assert filecmp.cmp(root / 'control/library.rgb', root / 'direct/library.rgb', shallow=False), 'Library pixel mismatch'
            if pair:
                pairs.append(row)
        report['profiles'][profile] = {'excluded_warmups_per_arm': 1, 'pairs': pairs, 'summary': summarize(pairs)}
    with output.open('x') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')


def replay(path):
    report = json.loads(path.read_text())
    assert report['unit'] == 'microseconds' and set(report['profiles']) == set(PROFILES)
    assert report['lifecycle'] == [[profile, arm, check] for profile in PROFILES
                                   for check in ('stop', 'suspend') for arm in ARMS]
    for profile in PROFILES:
        record = report['profiles'][profile]
        pairs = record['pairs']
        assert record['excluded_warmups_per_arm'] == 1 and len(pairs) == 8
        for number, pair in enumerate(pairs, 1):
            assert pair['pair'] == number and pair['order'] == list(ARMS if number % 2 else ARMS[::-1])
            for arm in ARMS:
                values = pair[arm]
                assert set(values) == set(INTERVALS) and all(type(value) is int and value >= 0 for value in values.values())
                assert values['attach_to_route'] <= values['attach_to_view'] < values['initial_to_library'] < values['entry_to_library']
        assert summarize(pairs) == record['summary']
    print('Verified both balanced schedules, all sixteen pairs and their four interval summaries.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='mode', required=True)
    running = subparsers.add_parser('run')
    running.add_argument('binary', type=Path)
    running.add_argument('output', type=Path)
    checking = subparsers.add_parser('check')
    checking.add_argument('result', type=Path)
    args = parser.parse_args()
    if args.mode == 'run':
        run(args.binary.resolve(strict=True), args.output)
    else:
        replay(args.result)
