"""Run the frozen pristine-stock versus frame-local-reuse optimized Gray8 screen on test-env."""

import argparse
import filecmp
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import tempfile


SIZES = {'scribe': (1860, 2480), 'pw6': (1264, 1680)}
COUNTERS = ['measure_builds', 'paint_builds', 'hits', 'different_width_hits', 'negative_checks',
            'cjk_hits', 'peak_entries', 'cloned_paragraphs']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def visit(programs, folder, profile, arm, cjk):
    folder.mkdir()
    environment = dict(os.environ, TZ='UTC', AIDOKU_ATTACH_TRIAL_CASE=str(folder),
                       AIDOKU_ATTACH_TRIAL_ARM='control', AIDOKU_ATTACH_TRIAL_CHECK='normal')
    environment.pop('AIDOKU_PRELINE_CJK', None)
    if cjk:
        environment['AIDOKU_PRELINE_CJK'] = '1'
    environment['AIDOKU_PRELINE_ARM'] = arm
    trace = folder / 'trace.json'
    binary = programs / ('stock' if arm == 'stock' else 'candidate')
    proc = subprocess.run([binary, '--offline', '--device', profile, '--data-dir', folder / 'data',
                           '--exit-after-ms', '100', '--performance-trace', trace], env=environment,
                          capture_output=True, timeout=30)
    require(proc.returncode == 0, 'Gray8 fixture failed a draw, generation, canonical pixel, or pointer assertion: '
            + proc.stderr.decode(errors='replace')[-800:])
    log = json.loads(trace.read_bytes())
    require(log['finished'] and log['success'] and not log['dropped_events'], 'Trace did not close successfully')
    def stamp(marker):
        time, = [row['at_us'] for row in log['events'] if row['name'] == marker]
        return time
    entry, first, ready, shelf, action = [stamp(name) for name in ('process_entry', 'attach_trial_first_full_draw',
        'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified')]
    require(entry < first < ready < shelf < action, 'Invalid render order')
    draw = int((folder / 'draw-ns').read_text()) / 1000
    width, height = SIZES[profile]
    damage = int((folder / 'damage').read_text())
    require((folder / 'library.gray').stat().st_size == width * height and 0 < damage <= width * height,
            'Incomplete canonical frame or unknown damage')
    require(0 < draw <= shelf - entry, 'Inconsistent software draw interval')
    record = {'draw_us': draw, 'entry_us': shelf - entry, 'damage_pixels': damage}
    if arm != 'stock':
        values = json.loads((folder / 'preline-counts').read_bytes())
        require(len(values) == len(COUNTERS) and all(type(value) is int and value >= 0 for value in values), 'Invalid counters')
        record.update(zip(COUNTERS, values))
        if arm == 'off':
            require(values[2:] == [0] * 6, 'Off mode retained a cache')
        else:
            require(record['cloned_paragraphs'] >= record['peak_entries'] > 0 and record['different_width_hits'] > 0
                    and record['cloned_paragraphs'] >= record['hits'] > 0, 'Missing cache use or retention')
            if cjk:
                require(record['cjk_hits'] > 0 and record['negative_checks'] == 4 * record['hits'], 'Missing correctness controls')
    return record


def pair(programs, profile, order, cjk=False):
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        arms = {arm: visit(programs, root / arm, profile, arm, cjk) for arm in order}
        require(filecmp.cmp(root / order[0] / 'library.gray', root / order[1] / 'library.gray', shallow=False), 'Gray8 pixels differ')
        require(arms[order[0]]['damage_pixels'] == arms[order[1]]['damage_pixels'], 'Gray8 damage changed')
    return dict(order=order, **arms, equal_gray_bytes=True, click_and_dismiss=True, next_draw_checked=True)


def evaluate(pairs, other='reuse'):
    result = {}
    for metric in ('draw_us', 'entry_us'):
        savings = [row['stock'][metric] - row[other][metric] for row in pairs]
        result[metric] = {'stock': median(row['stock'][metric] for row in pairs), other: median(row[other][metric] for row in pairs),
                          'paired_us': median(savings), 'wins': sum(value > 0 for value in savings),
                          'AB_us': median(savings[::2]), 'BA_us': median(savings[1::2])}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('programs', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), 'Use an exclusive fresh output')
    programs = args.programs.resolve(strict=True)
    result = {'scope': 'x86_64 release opt-level 3, LTO false, native retained Gray8 target; no panel, card, or hardware acceleration',
              'profiles': {}}
    for profile in SIZES:
        pair(programs, profile, ['stock', 'reuse'])
        timed = [pair(programs, profile, ['stock', 'reuse'] if index % 2 == 0 else ['reuse', 'stock']) for index in range(6)]
        stats = evaluate(timed)
        passed = stats['draw_us']['paired_us'] >= 1000 and stats['draw_us']['wins'] >= 5 and stats['entry_us']['paired_us'] >= -1000
        cjk = pair(programs, profile, ['stock', 'reuse'], cjk=True)
        off = [pair(programs, profile, ['stock', 'off']), pair(programs, profile, ['off', 'stock'])]
        original_builds = {row['off']['paint_builds'] for row in off}
        require(len(original_builds) == 1 and all(row['reuse']['paint_builds'] + row['reuse']['hits'] in original_builds for row in timed),
                'No corresponding reduction in the diagnostic paint path')
        result['profiles'][profile] = {'geometry': SIZES[profile], 'scale': 2, 'excluded_warmup_pairs': 1, 'timed': timed,
                                       'metrics': stats, 'passes': passed, 'cjk': cjk, 'stock_vs_off': off,
                                       'stock_vs_off_metrics': evaluate(off, 'off')}
    result['passes'] = all(profile['passes'] for profile in result['profiles'].values())
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2); stream.write('\n')


if __name__ == '__main__':
    main()
