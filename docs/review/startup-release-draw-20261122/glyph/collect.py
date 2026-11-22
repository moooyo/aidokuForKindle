"""Optional count-only comparison with an unchanged, identically seeded Gray8 reference."""

import argparse
import filecmp
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory


FIELDS = ('calls', 'distinct', 'hits', 'misses', 'preexisting_hit_keys', 'rendered', 'empty_alpha',
          'raster_none', 'misses_after_prior_none', 'misses_after_prior_cached', 'insertion_refused', 'evicted_entries')
PREFIX = b'AIDOKU_GLYPH_DIAG1 '


def counts(lines):
    rows = [line for line in lines.splitlines() if line.startswith(PREFIX)]
    if len(rows) != 1:
        raise ValueError('Missing or duplicate aggregate line')
    match = re.fullmatch(rb'AIDOKU_GLYPH_DIAG1 (\[[0-9, ]{12,160}\]) ([0-9]{1,8})', rows[0])
    if not match:
        raise ValueError('Invalid aggregate line')
    values = json.loads(match[1])
    if len(values) != len(FIELDS) or not all(type(value) == int and 0 <= value <= 8192 for value in values):
        raise ValueError('Invalid aggregate fields')
    result = dict(zip(FIELDS, values, strict=True), peak_alpha_bytes=int(match[2]))
    if not (0 < result['distinct'] <= result['calls'] == result['hits'] + result['misses']
            and result['misses'] == result['rendered'] + result['raster_none']
            and result['empty_alpha'] <= result['rendered'] and result['peak_alpha_bytes'] <= 1048576):
        raise ValueError('Inconsistent aggregate counts')
    return result


def visit(binary, directory, profile, locale, instrumented):
    directory.mkdir()
    env = dict(os.environ, TZ='UTC', AIDOKU_ATTACH_TRIAL_CASE=str(directory),
               AIDOKU_ATTACH_TRIAL_ARM='control', AIDOKU_ATTACH_TRIAL_CHECK='normal', AIDOKU_PRELINE_ARM='stock')
    for key in ('AIDOKU_PRELINE_CJK', 'AIDOKU_RELEASE_DRAW_PROFILE', 'LD_PRELOAD'):
        env.pop(key, None)
    if locale == 'cjk':
        env['AIDOKU_PRELINE_CJK'] = '1'
    trace = directory / 'trace.json'
    child = subprocess.run([binary, '--offline', '--device', profile, '--data-dir', directory / 'data',
                            '--exit-after-ms', '100', '--performance-trace', trace], capture_output=True, env=env, timeout=25)
    if child.returncode:
        raise ValueError('Fixture did not complete')
    result = json.loads(trace.read_bytes())
    if not (result['finished'] and result['success'] and not result['dropped_events']):
        raise ValueError('Incomplete fixture trace')
    expected = ['attach_trial_first_full_draw', 'startup_ready', 'attach_trial_first_library_draw', 'attach_trial_interaction_verified']
    if [event['name'] for event in result['events'] if event['name'] in expected] != expected:
        raise ValueError('Missing or unordered projection/interaction')
    if instrumented:
        return counts(child.stderr)
    if PREFIX in child.stderr:
        raise ValueError('Reference is instrumented')
    return None


def collect(stock, diagnostic):
    if stock.samefile(diagnostic):
        raise ValueError('A separate unmodified stock binary is required')
    rows = []
    for profile, pixel_bytes in (('scribe', 1860 * 2480), ('pw6', 1264 * 1680)):
        for locale in ('english', 'cjk'):
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                visit(stock, root / 'stock', profile, locale, False)
                stats = visit(diagnostic, root / 'observed', profile, locale, True)
                reference = root / 'stock/library.gray'
                if not (reference.stat().st_size == pixel_bytes and filecmp.cmp(reference, root / 'observed/library.gray', shallow=False)
                        and (root / 'stock/damage').read_bytes() == (root / 'observed/damage').read_bytes()):
                    raise ValueError('Gray8 pixels or damage differ')
                rows.append(dict(profile=profile, fixture=locale, pixel_parity=True, reversible_input=True, counts=stats))
    return {'scope': 'Aggregate diagnostic only; no speed claim or native device use', 'cases': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('diagnostic', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists')
    result = collect(args.stock.resolve(strict=True), args.diagnostic.resolve(strict=True))
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
