"""Capture per-thread hardware samples only inside real complete release Gray8 draws."""

import argparse
from collections import Counter
import filecmp
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile


PERIOD = 2_000_000
PROFILES = {'scribe': (1860, 2480), 'pw6': (1264, 1680)}


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def records(raw, inner, duration, diagnostic=None):
    need(len(raw) >= 72, 'truncated_header')
    version, pid, start_before, start_after, end_before, end_after, total, payload, period = struct.unpack_from('<9Q', raw)
    begin, end = inner
    need(version == 1 and period == PERIOD and pid > 0 and payload == len(raw) - 72 and 0 < payload <= 262144, 'invalid_header')
    need(0 < start_before <= start_after <= begin < end <= end_before <= end_after and total > 0, 'inconsistent_clocks')
    span = end - begin
    need(abs(duration - span) <= max(150_000, duration * .05), 'elapsed_disagreement')
    samples, clipped = [], 0
    offset = 72
    while offset < len(raw):
        need(len(raw) - offset >= 8, 'truncated_record')
        kind, misc, size = struct.unpack_from('<IHH', raw, offset)
        need(kind == 9, f'non_sample_record_{kind}')
        need(size >= 48 and offset + size <= len(raw) and (misc & 7) in (1, 2), 'invalid_record')
        ip, process, thread, time, weight, depth = struct.unpack_from('<QIIQQQ', raw, offset + 8)
        need(process == thread == pid and ip > 0 and weight == PERIOD and 0 < depth <= 128 and size == 48 + 8 * depth,
             'wrong_thread_period_or_stack')
        need(start_before <= time <= end_after, 'sample_outside_gate')
        chain = struct.unpack_from(f'<{depth}Q', raw, offset + 48)
        if begin < time < end:
            frames = (ip, *(address - 1 for address in chain if 0 < address < 1 << 63 and address != ip)) if (misc & 7) == 2 else (0,)
            samples.append((time, weight, frames))
        else:
            clipped += 1
        offset += size
    times = [time for time, _, _ in samples]
    if diagnostic is not None:
        diagnostic.update(retained_sample_count=len(times), boundary_sample_count=clipped)
    need(times == sorted(times), 'unordered_samples')
    need(len(times) >= 4, 'too_few_samples')
    bins = [0] * 4
    for time in times:
        bins[(time - begin) * 4 // span] += 1
    coverage = (times[-1] - times[0]) / span
    weights = Counter()
    for _, weight, chain in samples:
        weights[chain] += weight
    stats = {'samples': len(samples), 'clipped': clipped, 'bins': bins, 'sample_span': round(coverage, 5),
             'first_sample_us': round((times[0] - begin) / 1000, 3), 'last_gap_us': round((end - times[-1]) / 1000, 3),
             'max_internal_gap_us': round(max(b - a for a, b in zip(times, times[1:])) / 1000, 3),
             'period_weight_to_enabled_cycles': round(sum(weights.values()) / total, 5), 'draw_us': round(span / 1000, 3),
             'no_physical_ancestor': sum(weight // PERIOD for chain, weight in weights.items() if len(chain) == 1)}
    return weights, stats


def mappings(path):
    result = []
    for row in path.read_text().splitlines():
        fields = row.split(None, 5)
        if len(fields) == 6 and 'x' in fields[1]:
            low, high = (int(part, 16) for part in fields[0].split('-'))
            result.append((low, high, int(fields[2], 16), fields[5]))
    return result


def elf_segments(binary):
    with binary.open('rb') as stream:
        header = stream.read(64)
        need(header[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', header, 18)[0] == 62, 'unsupported_elf')
        start, = struct.unpack_from('<Q', header, 32)
        step, entries = struct.unpack_from('<HH', header, 54)
        result = []
        for index in range(entries):
            stream.seek(start + step * index)
            kind, flags, offset, address, _, size, _, _ = struct.unpack('<II6Q', stream.read(56))
            if kind == 1 and flags & 1:
                result.append((offset, offset + size, address))
    return result


def relative_samples(weights, regions, binary):
    segments = elf_segments(binary)
    classified = Counter()
    def key(address):
        if address == 0:
            return 'kernel_skid'
        for low, high, file_offset, filename in regions:
            if low <= address < high:
                if filename == str(binary):
                    offset = address - low + file_offset
                    selected = [hex(base + offset - start) for start, end, base in segments if start <= offset < end]
                    return selected[0] if len(selected) == 1 else 'unmapped'
                return 'external'
        return 'unmapped'
    for chain, weight in weights.items():
        classified[tuple(key(address) for address in chain)] += weight
    return [[list(chain), weight] for chain, weight in classified.items()]


def visit(binary, helper, root, profile, enabled):
    root.mkdir()
    trace = root / 'trace.json'
    env = dict(os.environ, TZ='UTC', AIDOKU_ATTACH_TRIAL_CASE=str(root), AIDOKU_ATTACH_TRIAL_ARM='control',
               AIDOKU_ATTACH_TRIAL_CHECK='normal')
    for name in ('LD_PRELOAD', 'AIDOKU_FRAME_CYCLES', 'AIDOKU_FRAME_MAPS', 'AIDOKU_RELEASE_DRAW_PROFILE'):
        env.pop(name, None)
    if enabled:
        env.update(LD_PRELOAD=str(helper), AIDOKU_FRAME_CYCLES=str(root/'samples'), AIDOKU_FRAME_MAPS=str(root/'maps'), AIDOKU_RELEASE_DRAW_PROFILE='1')
    proc = subprocess.run([binary, '--offline', '--device', profile, '--data-dir', root/'data', '--exit-after-ms', '100',
                           '--performance-trace', trace], env=env, timeout=30, capture_output=True)
    need(proc.returncode == 0, f'fixture_status_{proc.returncode}')
    record = json.loads(trace.read_bytes())
    sequence = ['process_entry', 'attach_trial_first_full_draw', 'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified']
    names = [event['name'] for event in record['events']]
    need(record['finished'] and record['success'] and not record['dropped_events'] and all(names.count(n) == 1 for n in sequence)
         and [names.index(n) for n in sequence] == sorted(names.index(n) for n in sequence), 'missing_same_visit_witness')
    width, height = PROFILES[profile]
    need(int((root/'damage').read_text()) == width*height == (root/'library.gray').stat().st_size, 'incomplete_gray_damage')


def collect(binary, helper, output):
    binary = binary.resolve(strict=True); helper = helper.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    for profile in PROFILES:
        visit(binary, helper, output/f'reference-{profile}', profile, False)
    for number in range(48):
        for profile in (PROFILES if number % 2 == 0 else reversed(PROFILES)):
            with tempfile.TemporaryDirectory() as temp:
                case = Path(temp)/'visit'
                diagnostic = {}
                try:
                    visit(binary, helper, case, profile, True)
                    need(filecmp.cmp(case/'library.gray', output/f'reference-{profile}'/'library.gray', shallow=False), 'different_complete_gray_frame')
                    begin, end = (int(text) for text in (case/'draw-window').read_text().split())
                    data = (case/'samples').read_bytes()
                    diagnostic['draw_us'] = round((end - begin) / 1000, 3)
                    diagnostic['raw_sample_count'] = 0
                    offset = 72
                    while offset + 8 <= len(data):
                        kind, _, size = struct.unpack_from('<IHH', data, offset)
                        if size < 8 or offset + size > len(data):
                            break
                        diagnostic['raw_sample_count'] += int(kind == 9)
                        offset += size
                    weights, stats = records(data, (begin, end), int((case/'draw-ns').read_text()), diagnostic)
                    value = dict(valid=True, stats=stats, private_chains=relative_samples(weights, mappings(case/'maps'), binary))
                except (ValueError, subprocess.TimeoutExpired) as error:
                    value = dict(valid=False, refusal='timeout' if isinstance(error, subprocess.TimeoutExpired) else str(error), **diagnostic)
                (output/f'{profile}-{number+1:02}.json').write_text(json.dumps(value)+'\n')
    print('Completed all 96 scheduled attempts; retained records are private pending symbolization.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('binary', 'helper', 'private_output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    collect(args.binary, args.helper, args.private_output)
