"""One stock/transparent test using the real release, 2x retained Gray8 six-book fixture."""

import argparse
import filecmp
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import tempfile


SHAPES = {'scribe': (1860, 2480), 'pw6': (1264, 1680)}
ARMS = ('paper', 'transparent')
STATES = ('plain', 'pressed', 'filter', 'dismissed', 'night', 'settings', 'night-return', 'light-return', 'selection', 'deselected')


def visit(programs, folder, profile, arm, language=None):
    folder.mkdir()
    env = dict(os.environ, TZ='UTC', AIDOKU_ATTACH_TRIAL_CASE=str(folder), AIDOKU_ATTACH_TRIAL_ARM='control',
               AIDOKU_ATTACH_TRIAL_CHECK='normal')
    for key in ('LD_PRELOAD', 'AIDOKU_PRELINE_ARM', 'AIDOKU_PRELINE_CJK', 'AIDOKU_COMPOSITION_STATES', 'AIDOKU_RELEASE_DRAW_PROFILE'):
        env.pop(key, None)
    if language:
        env['AIDOKU_COMPOSITION_STATES'] = '1'
        if language == 'zh-CN':
            env['AIDOKU_PRELINE_CJK'] = '1'
    trace = folder / 'trace.json'
    proc = subprocess.run([programs / arm, '--device', profile, '--offline', '--data-dir', folder / 'data',
                           '--exit-after-ms', '1000' if language else '100', '--performance-trace', trace],
                          env=env, capture_output=True, timeout=20)
    if proc.returncode:
        raise ValueError(proc.stderr.decode(errors='replace')[-1400:])
    log = json.loads(trace.read_bytes())
    assert log['finished'] and log['success'] and log['dropped_events'] == 0
    def stamp(name):
        clock, = [event['at_us'] for event in log['events'] if event['name'] == name]
        return clock
    main, splash, ready, shelf, acted = map(stamp, ('process_entry', 'attach_trial_first_full_draw', 'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified'))
    width, height = SHAPES[profile]
    assert main < splash < ready < shelf < acted and (folder / 'library.gray').stat().st_size == width * height
    assert int((folder / 'damage').read_text()) == width * height
    draw = int((folder / 'draw-ns').read_text()) / 1000
    assert 0 < draw < shelf - main
    return {'draw_us': draw, 'entry_us': shelf - main}


def pair(programs, profile, order, record, language=None):
    record['order'] = list(order)
    width, height = SHAPES[profile]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for arm in order:
            record[arm] = visit(programs, base / arm, profile, arm, language)
        expected = ('library', *STATES) if language else ('library',)
        for name in expected:
            left, right = [base / arm / f'{name}.gray' for arm in ARMS]
            assert left.stat().st_size == right.stat().st_size == width * height and filecmp.cmp(left, right, shallow=False), f'Pixel mismatch: {name}'
        if language:
            record['damage'] = {arm: (base / arm / 'state-damage').read_text().splitlines() for arm in ARMS}
            record['state_frames_equal'] = list(STATES)
        record['complete_gray_equal'] = True


def summary(pairs):
    values = {}
    for metric in ('draw_us', 'entry_us'):
        left, right = ([row[arm][metric] for row in pairs] for arm in ARMS)
        gain = [a-b for a, b in zip(left, right)]
        values[metric] = {'stock_us': median(left), 'candidate_us': median(right), 'paired_saving_us': median(gain),
                          'wins': sum(delta > 0 for delta in gain), 'AB_saving_us': median(gain[::2]), 'BA_saving_us': median(gain[1::2])}
    return values


def run(programs, output):
    results = {key: {'pairs': [], 'guards': {}} for key in SHAPES}
    report = {'profiles': results, 'clock': 'x86 release host RetainedScene::render, Gray8 at 2x', 'scope': 'No native card, framebuffer or e-ink',
              'cpu_affinity': sorted(os.sched_getaffinity(0))}
    with output.open('x') as stream:
        try:
            for profile in SHAPES:
                records = results[profile]
                for lang in ('en', 'zh-CN'):
                    row = records['guards'][lang] = {}
                    pair(programs, profile, ARMS, row, lang)
                for index in range(7):
                    row = {'pair': index}
                    records['pairs'].append(row)
                    pair(programs, profile, ARMS if index % 2 else ARMS[::-1], row)
                stats = records['summary'] = summary(records['pairs'][1:])
                records['passes'] = stats['draw_us']['paired_saving_us'] >= 1000 and stats['draw_us']['wins'] >= 5 and stats['entry_us']['paired_saving_us'] >= -1000
            report['passes'] = all(group['passes'] for group in results.values())
            report['status'] = 'complete'
        except BaseException as error:
            report['status'] = 'stopped'
            report['reason'] = str(error)[:300]
            raise
        finally:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('programs', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    run(args.programs.resolve(strict=True), args.output)
