"""Profile only the first Library software draw of the category-count control on test-env."""

import argparse
from collections import Counter
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET


PERIOD = 2_000_000
SIZES = {'scribe': (930, 1240), 'pw6': (632, 840)}
GROUPS = {
    'glyph_raster': ('swash::scale::', 'skrifa::outline::', 'zeno::raster::'),
    'svg_raster': ('resvg::render', 'resvg::path::', 'usvg::parser::'),
    'text_layout_shape': ('::text_layout_info', '::text_size', 'sharedparley::shape_paragraphs',
                          'parley::shape::', 'harfrust::hb::', 'fonts::match_font', 'fontique::'),
    'pixel_composition': ('::process_rectangle_impl', '::process_rounded_rectangle', '::draw_texture_line',
                          '::fill_background', '::foreach_ranges', '::process_scene_texture', '::spec_fill'),
    'component_instantiation': ('::ensure_instantiated', '::ensure_updated', '::register_item_tree'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def execute(args, **kwargs):
    return subprocess.run(args, capture_output=True, check=True, timeout=90, **kwargs).stdout


def attributed(report):
    nodes, parents = [], []
    for line in report.splitlines():
        match = re.fullmatch(r'\s*([0-9]+) ([ |]*)\+-(.+)', line)
        if not match:
            continue
        cost, depth, symbol = int(match[1]), len(match[2]), match[3]
        while parents and parents[-1][0] >= depth:
            parents.pop()
        parent = nodes[parents[-1][1]] if parents else None
        require(parent is not None or not nodes and symbol == '<Total>', 'Unrecognized call tree root')
        if parent:
            parent[0] -= cost
        inside = bool(parent and parent[2]) or symbol.startswith('<i_slint_renderer_software::SoftwareRenderer>::render')
        group = parent[1] if parent else 'outside_renderer'
        if inside:
            if group == 'outside_renderer':
                group = 'other_renderer'
            owner = symbol.partition('::<')[0].partition('::{closure')[0]
            if not owner.startswith(('<core::', '<std::', 'core::', 'std::', '<alloc::', 'alloc::')):
                group = next((label for label, names in GROUPS.items() if any(name in owner for name in names)), group)
        elif '__collector_hwc_samples_lost' in symbol:
            group = 'collector_lost'
        parents.append((depth, len(nodes)))
        nodes.append([cost, group, inside])
        if len(nodes) == 1:
            total = cost
    require(nodes and all(node[0] >= 0 for node in nodes), 'Truncated or inconsistent call tree')
    result = Counter()
    for weight, label, _ in nodes:
        result[label] += weight
    require(sum(result.values()) == total and total > 0, 'Cycle totals do not reconcile')
    return dict(sorted(result.items()))


def checked_samples(experiment, render_ns):
    root = ET.fromstring(b'<log>' + (experiment / 'log.xml').read_bytes().rstrip(b'\0\n') + b'</log>')
    require(root.find('experiment').get('version') == '12.4', 'Unsupported gprofng record format')
    clock = lambda event: int(Decimal(event.get('tstamp')) * 1_000_000_000)
    origin = clock(root.find("event[@kind='run']"))
    gates = [event for event in root.iter('event') if event.get('kind') in ('pause', 'resume')]
    require([event.get('kind') for event in gates] == ['pause', 'resume', 'pause'], 'Expected just one gated draw')
    begin, end = (origin + clock(event) for event in gates[1:])
    require(0 < render_ns and abs(end - begin - render_ns) < max(5_000_000, render_ns // 10), 'Gate and render disagree')
    pid = int(root.find('process[@pid]').get('pid'))
    records = (experiment / 'hwcounters').read_bytes()
    require(len(records) % 64 == 0, 'Invalid gprofng hardware sample file')
    times, weights = [], Counter()
    for header, _, lwp, _, _, stamp, _, _, count in struct.iter_unpack('<II7Q', records):
        if header == 0x30040:
            weights[lwp] += count
            if lwp == pid:
                times.append(stamp)
    weight = weights[pid]
    require(len(times) >= 25 and sum(value == weight for value in weights.values()) == 1, 'Ambiguous GUI thread samples')
    span = end - begin
    coverage = (max(times) - min(times)) / span
    require(min(times) >= begin and max(times) <= end and coverage >= .9, 'Insufficient frame coverage')
    return len(times), round(coverage, 4), weight


def visit(binary, profile, folder):
    trace = folder / 'trace.json'
    experiment = folder / 'frame.er'
    env = dict(os.environ, AIDOKU_ATTACH_TRIAL_CASE=str(folder), AIDOKU_ATTACH_TRIAL_ARM='control',
               AIDOKU_ATTACH_TRIAL_CHECK='normal', AIDOKU_FIRST_DRAW_GPROFNG='1', TZ='UTC')
    execute(['gprofng', 'collect', 'app', '-j', 'off', '-a', 'off', '-F', 'off', '-p', 'off',
             '-h', f'cycles,{PERIOD}', '-y', 'URG', '-o', experiment, binary, '--device', profile,
             '--data-dir', folder / 'data', '--offline', '--exit-after-ms', '100', '--performance-trace', trace], env=env)
    report = json.loads(trace.read_bytes())
    require(report['finished'] and report['success'] and report['dropped_events'] == 0, 'Incomplete fixture run')
    names = [event['name'] for event in report['events']]
    markers = ['attach_trial_first_full_draw', 'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified']
    require(all(names.count(name) == 1 for name in markers) and [names.index(name) for name in markers] == sorted(names.index(name) for name in markers), 'Missing or reordered shelf/interaction milestones')
    width, height = SIZES[profile]
    require((folder / 'library.rgb').stat().st_size == width * height * 3, 'Wrong software frame geometry')
    render_ns = int((folder / 'first-render-ns').read_text())
    samples, coverage, gui_weight = checked_samples(experiment, render_ns)
    listing = execute(['gprofng', 'display', 'text', '-thread_list', experiment]).decode()
    thread_count = int(re.search(r'^\s*1\s+all\s+(\d+)\s*$', listing, re.M)[1])
    for thread in range(1, thread_count + 1):
        stack = execute(['gprofng', 'display', 'text', '-filter', f'THRID=={thread}', '-metrics', 'e+cycles:name', '-calltree', experiment]).decode()
        if not re.search(rf'^\s*{gui_weight}\s+\+-<Total>$', stack, re.M):
            continue
        groups = attributed(stack)
        require(sum(groups.values()) == gui_weight, 'Unreconciled main-thread call tree')
        return {'main_thread_samples': samples, 'frame_sample_span': coverage, 'estimated_cycles': groups}
    raise ValueError('No displayed thread matches the sampled GUI TID and weight')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--runs', type=int, default=4)
    args = parser.parse_args()
    require(1 <= args.runs <= 12 and not args.output.exists(), 'Expected fresh output and 1 to 12 runs per profile')
    binary = args.binary.resolve(strict=True)
    runs = {profile: [] for profile in SIZES}
    for trial in range(args.runs):
        for profile in (SIZES if trial % 2 == 0 else reversed(SIZES)):
            with tempfile.TemporaryDirectory() as scratch:
                runs[profile].append(visit(binary, profile, Path(scratch)))
    with args.output.open('x') as output:
        json.dump({'scope': 'test-env x86_64 unoptimized RGB8 software fixture; gated main-thread CPU cycles, not elapsed device time',
                   'period_cycles': PERIOD, 'visits': runs}, output, indent=2)
        output.write('\n')


if __name__ == '__main__':
    main()
