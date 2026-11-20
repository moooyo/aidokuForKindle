"""Summarize private, read-only cache-index observations from the light GUI cohort."""

import argparse
import json
from pathlib import Path


def require(value):
    if not value:
        raise ValueError('Incomplete or inconsistent cache observations')


def roster(path):
    rows = {}
    for line in path.read_text().splitlines():
        key, size, checksum, fingerprint, last_access, valid, cover = line.split('|')
        require(key not in rows and valid in ('0', '1') and cover in ('0', '1'))
        rows[key] = (int(size), checksum, fingerprint, int(last_access), valid, cover)
    return rows


def summarize(batch):
    result = json.loads((batch / 'result.json').read_bytes())
    require(result['batch'] == batch.name and result['planned'] == result['passed_cases'] == 6)
    require(all(result[key] is True for key in ('low_observer', 'lease_released', 'operation_succeeded',
                                                'original_state_restored_or_unmodified')))
    ops = result['operations']
    require(all(not op['timeout'] and op['exit'] == 0 for op in ops))
    names = [op['operation'] for op in ops]
    require(len(names) == len(set(names)))
    snapshots = []
    for i in range(7):
        name = f'cache-roster-{i}'
        index = names.index(name)
        require((i == 0 or names.index(f'collect-{i}') < index)
                and (i == 6 or index < names.index(f'visit-{i+1}')))
        snapshots.append(roster(batch / f'{index:02d}-{name}.stdout'))
    visits = []
    for i, (before, after) in enumerate(zip(snapshots, snapshots[1:]), 1):
        common = before.keys() & after.keys()
        touched = [k for k in common if after[k][3] > before[k][3]]
        visits.append({
            'visit': i,
            'processed_records_added': len(after.keys() - before.keys()),
            'processed_records_removed': len(before.keys() - after.keys()),
            'records_changed_excluding_access': sum(before[k][:3] + before[k][4:] != after[k][:3] + after[k][4:]
                                                    for k in common),
            'existing_valid_cover_records_with_advanced_access': sum(after[k][4:] == ('1', '1') for k in touched),
            'other_records_with_advanced_access': sum(after[k][4:] != ('1', '1') for k in touched),
        })
    return {'scope': 'Read-only processed-artifact index snapshots with the app stopped. Each after-snapshot was taken after the visit and outside the timed interval. Access bookkeeping is not a per-book hit or decode trace. No conclusion about the excluded first visit follows from these later snapshots.',
            'visits': visits}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    print(json.dumps(summarize(parser.parse_args().batch), indent=2))
