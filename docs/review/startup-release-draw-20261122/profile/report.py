"""Symbolize the exact frozen release ELF and export aggregate stage weights, never user addresses."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

from measure import PERIOD, PROFILES, need


STAGES = ('glyph_mask_raster', 'glyph_path_lookup', 'text_measure_shape', 'pixel_composition', 'svg', 'other', 'unresolved')


def stage(name, source):
    if ('/swash-' in source and '/scale/' in source or '/skrifa-' in source and '/outline/' in source
            or '/zeno-' in source and '/raster' in source):
        return 'glyph_mask_raster'
    if '/fonts/vectorfont.rs' in source and ('render_vector_glyph' in name or '::render_glyph' in name):
        return 'glyph_path_lookup'
    if any(f'/{crate}-' in source for crate in ('usvg', 'resvg')):
        return 'svg'
    if ('/gray_target.rs' in source and any(term in name for term in ('::blend', '::from_rgb', 'observe_color'))
            or 'i-slint-renderer-software-' in source and ('/draw_functions.rs' in source or any(term in name for term in (
                '::process_rectangle_impl', '::process_rectangle', '::process_simple_rectangle', '::process_rounded_rectangle',
                '::process_scene_texture', '::process_texture_impl', '::process_target_texture', '::foreach_ranges', '::fill_background')))
            or '::spec_fill' in name and 'Gray8Pixel' in name):
        return 'pixel_composition'
    if (any(f'/{crate}-' in source for crate in ('harfrust', 'fontique', 'icu_segmenter'))
            or '/parley-' in source and any(part in source for part in ('/shape/', '/shape.rs', '/analysis', '/builder', '/resolve', '/line/'))
            or 'i-slint-core-' in source and any(term in name for term in ('::text_layout_info', '::text_size', '::shape_paragraphs',
                '::create_text_paragraphs', '::get_or_create_text_paragraphs', 'LayoutWithoutLineBreaksBuilder', '::ranged_builder'))
            or 'i-slint-renderer-software-' in source and ('::match_font' in name or '::text_size' in name)):
        return 'text_measure_shape'
    return None


def lookup(binary, addresses):
    addresses = sorted(addresses, key=lambda value: int(value, 16))
    output = subprocess.run(['addr2line', '-a', '-f', '-i', '-C', '-e', binary], input='\n'.join(addresses)+'\n',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=180).stdout.splitlines()
    symbols = {}
    index = 0
    for address in addresses:
        need(index < len(output) and int(output[index], 16) == int(address, 16), 'symbol_order')
        index += 1
        frames = []
        while index < len(output) and not re.fullmatch(r'0x[0-9a-fA-F]+', output[index]):
            need(index + 1 < len(output), 'incomplete_symbol')
            frames.append((output[index], output[index + 1])); index += 2
        symbols[address] = frames
    need(index == len(output), 'trailing_symbols')
    return symbols


def classify(chain, symbols):
    observed_project_stage = False
    known_leaf = any(name != '??' and not line.startswith('??') for name, line in symbols.get(chain[0], []))
    inlined_leaf = len(symbols.get(chain[0], [])) > 1
    if chain[0] == 'kernel_skid':
        return 'unresolved', 'kernel_skid', known_leaf, inlined_leaf
    for address in chain:
        for name, source in symbols.get(address, []):
            category = stage(name, source)
            if category:
                return category, None, known_leaf, inlined_leaf
            if any(term in source for term in ('i-slint-core-', 'i-slint-renderer-software-', '/gray_target.rs',
                                                '/app.rs', '/app.slint', '/attach_trial.rs', '/appwindow.rs', '/app_window.rs')):
                observed_project_stage = True
    if observed_project_stage:
        return 'other', None, known_leaf, inlined_leaf
    return 'unresolved', ('external' if chain[0] == 'external' else 'unmapped' if chain[0] == 'unmapped' else 'no_stage_context'), known_leaf, inlined_leaf


def evaluate(root, binary, control):
    visits = {profile: [json.loads((root/f'{profile}-{number:02}.json').read_text()) for number in range(1,49)] for profile in PROFILES}
    all_addresses = {address for rows in visits.values() for row in rows if row['valid'] for chain, _ in row['private_chains']
                     for address in chain if address.startswith('0x')}
    symbols = lookup(binary, all_addresses) if all_addresses else {}
    result = {'scope': 'x86_64 opt-3 release Gray8 first software draw; sampled user cycles, not Kindle or elapsed time',
              'period_cycles': PERIOD, 'preflight_control': json.loads(control.read_text()), 'profiles': {}}
    for profile, rows in visits.items():
        bins = [0, 0, 0, 0]; total = Counter(); unknown = Counter(); code_quality = Counter(); public = []
        for number, row in enumerate(rows, 1):
            if not row['valid']:
                public.append({'visit': number, **row}); continue
            stats = row['stats']; counts = Counter(); bad = Counter(); explained = Counter()
            for chain, weight in row['private_chains']:
                need(weight > 0 and weight % PERIOD == 0, 'invalid_period_weight')
                count = weight // PERIOD
                label, why, known, inlined = classify(chain, symbols)
                counts[label] += count
                if why: bad[why] += count
                if known: explained['leaf_with_dwarf'] += count
                if inlined: explained['leaf_with_inline_chain'] += count
            need(sum(counts.values()) == stats['samples'] == sum(stats['bins']), 'unreconciled_samples')
            total.update(counts); unknown.update(bad); code_quality.update(explained)
            bins = [old + add for old, add in zip(bins, stats['bins'])]
            public.append({'visit': number, 'valid': True, **stats, 'counts': {stage: counts[stage] for stage in STAGES}})
        sample_count = sum(total.values()); valid_count = sum(row['valid'] for row in rows)
        accepted = valid_count >= 40 and sample_count >= 300 and all(.1 * sample_count <= count <= .4 * sample_count for count in bins)
        result['profiles'][profile] = {'visits': public, 'valid_visits': valid_count, 'eligible_samples': sample_count,
            'quartiles': bins, 'gate_passes': accepted,
            'percentages': {key: round(100 * total[key] / sample_count, 2) for key in STAGES} if accepted else None,
            'unresolved_samples': dict(unknown), 'symbol_quality': dict(code_quality)}
    return result


def publish(value, path):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def check_public(value):
    need(value['period_cycles'] == PERIOD and set(value['profiles']) == set(PROFILES), 'invalid_public_headers')
    for profile in value['profiles'].values():
        rows = profile['visits']; valid = [row for row in rows if row['valid']]
        need([row['visit'] for row in rows] == list(range(1,49)) and len(valid) == profile['valid_visits'], 'replaced_or_missing_visit')
        totals = Counter(); bins = [0] * 4
        for row in valid:
            need(set(row['counts']) == set(STAGES) and row['samples'] == sum(row['counts'].values()) == sum(row['bins'])
                 and row['samples'] >= 4, 'invalid_public_counts')
            totals.update(row['counts']); bins = [old + add for old,add in zip(bins,row['bins'])]
        count = sum(totals.values())
        passed = len(valid)>=40 and count>=300 and all(count*.1<=n<=count*.4 for n in bins)
        expected = {key: round(100 * totals[key] / count, 2) for key in STAGES} if passed else None
        need(count == profile['eligible_samples'] and bins == profile['quartiles'] and passed == profile['gate_passes']
             and profile['percentages'] == expected and sum(profile['unresolved_samples'].values()) == totals['unresolved'], 'invalid_public_summary')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('private_records','binary','control','public_output'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    result = evaluate(args.private_records, args.binary, args.control)
    check_public(result)
    publish(result, args.public_output)
