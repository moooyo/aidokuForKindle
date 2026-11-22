"""Recompute the single-candidate decision and its separate fill-count attribution."""

import json
from pathlib import Path
import sys

from count import MODELS
from screen import ARMS, STATES, summary


def read(directory, name):
    return json.loads((directory / name).read_bytes())


def verify(directory):
    result, stock, candidate = (read(directory, name) for name in ('result.json', 'stock-census.json', 'candidate-census.json'))
    assert all(data['status'] == 'complete' and set(data['profiles']) == set(MODELS) for data in (result, stock, candidate))
    assert len(result['cpu_affinity']) == 1
    for profile, (width, height, top, bottom) in MODELS.items():
        record = result['profiles'][profile]
        full = width * height
        assert set(record['guards']) == {'en', 'zh-CN'}
        for guard in record['guards'].values():
            assert guard['complete_gray_equal'] and tuple(guard['state_frames_equal']) == STATES
            for arm in ARMS:
                items = [(name, int(size)) for name, size in (line.split() for line in guard['damage'][arm])]
                assert tuple(name for name, _ in items) == STATES[1:] and all(0 < size <= full for _, size in items)
                assert 0 < items[0][1] < full
        pairs = record['pairs']
        assert len(pairs) == 7
        for index, row in enumerate(pairs):
            assert row['pair'] == index and row['order'] == list(ARMS if index % 2 else ARMS[::-1]) and row['complete_gray_equal']
            assert all(0 < row[arm]['draw_us'] <= row[arm]['entry_us'] for arm in ARMS)
        stats = summary(pairs[1:])
        assert stats == record['summary']
        assert record['passes'] == (stats['draw_us']['paired_saving_us'] >= 1000 and stats['draw_us']['wins'] >= 5 and stats['entry_us']['paired_saving_us'] >= -1000)
        original, changed = [report['profiles'][profile]['raw_counter'] for report in (stock, candidate)]
        for counter in (original, changed):
            assert (counter['width'], counter['height'], counter['paper']) == (width, height, 255)
            assert counter['transitions'] == 2 and counter['overflow'] == 0
        assert original['runs'] == [[-1, height], [-1, height]] and original['whole_runs'] == 2
        assert changed['runs'] == [[0, height], [0, top], [height - bottom, bottom]] and changed['whole_runs'] == 1
        assert original['full_width_fills'] - changed['full_width_fills'] == height - top - bottom
    assert result['passes'] == all(record['passes'] for record in result['profiles'].values())
    print(f"Verified both complete schedules, all retained state checks and both fill deltas; passes={result['passes']}")


if __name__ == '__main__':
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent)
