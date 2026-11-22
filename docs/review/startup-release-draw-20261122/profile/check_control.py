"""Verify exact reader/clock/symbol identity independently using three native C work regions."""

import argparse
from collections import Counter
import json
from pathlib import Path
import struct

from measure import mappings, need, records, relative_samples
from report import lookup


def counts(raw):
    kinds = Counter(); offset = 72
    while offset < len(raw):
        kind, _, size = struct.unpack_from('<IHH', raw, offset)
        need(size >= 8 and offset + size <= len(raw), 'invalid_control_record')
        kinds[kind] += 1; offset += size
    return kinds


def verify(binary, root, first=None):
    raw = (root/'gated2.raw').read_bytes()
    times = tuple(map(int, (root/'gated2.window').read_text().split()))
    weights, stats = records(raw, times, times[1] - times[0]); rows = relative_samples(weights, mappings(root/'gated2.maps'), binary)
    addresses = {chain[0] for chain, _ in rows if chain[0].startswith('0x')}
    symbols = lookup(binary, addresses)
    observed = Counter()
    unresolved = 0
    for chain, weight in rows:
        frames = symbols.get(chain[0], [])
        if not frames:
            unresolved += weight
            continue
        name = frames[0][0]
        need(name == 'region_inside', 'sampled_nonmiddle_control')
        observed[name] += weight
    accepted = sum(observed.values()); need(accepted > 0 and accepted >= 9 * unresolved, 'ambiguous_middle_control')
    result = {'v2_samples': stats['samples'], 'v2_named_work_in_gate_only': True, 'v2_throttles': counts(raw)[5],
              'v2_inside_work_samples': accepted // 2_000_000, 'v2_unresolved_samples': unresolved // 2_000_000,
              'v2_sample_span': stats['sample_span']}
    if first:
        rejected = counts(first.read_bytes()); need(rejected[5] > 0, 'historical_control_not_throttled')
        result.update(v1_throttles=rejected[5], v1_unthrottles=rejected[6], v1_consumed_app_visits=0)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary','root','output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--rejected', type=Path)
    args = parser.parse_args()
    with args.output.open('x') as stream:
        json.dump(verify(args.binary.resolve(strict=True), args.root, args.rejected), stream, indent=2); stream.write('\n')
