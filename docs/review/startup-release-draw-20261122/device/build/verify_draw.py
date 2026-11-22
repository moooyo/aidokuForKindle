"""Qualify the new ARM logger and optional controls without contacting a Kindle."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


TARGET = 'armv7-unknown-linux-musleabihf'
CASES = ('scribe', 'pw6', 'cpu-missing', 'counters-missing', 'both-missing', 'missing-frame', 'bad-marker', 'initial')
KEYS = ('sequence', 'marker', 'submitted_us', 'wall_us', 'thread_us', 'minflt', 'majflt', 'nvcsw', 'nivcsw',
        'width', 'height', 'format')


def run(*arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=150)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-3500:])
    return result


def need(ok, message):
    if not ok:
        raise ValueError(message)


def unique(where, pattern):
    results = list(where.glob(pattern))
    need(len(results) == 1, f'Expected one {pattern} in {where}')
    return results[0]


def fields(line):
    return dict(item.split('=', 1) for item in line.split()[2:])


def cli(program, marker):
    data = program.read_bytes()
    need((b'aidoku: startup_draw_cpu sequence=' in data) == marker, 'Incorrect feature gating of draw-CPU logger')
    need(b'aidoku: startup_transition sequence=' in data, 'Missing the previously qualified transition log')
    header = run('readelf', '-h', '-l', '-A', program).stdout
    need(all(part in header for part in (b'ELF32', b'little endian', b'ARM', b'Version5 EABI', b'hard-float ABI',
                                         b'Tag_CPU_arch: v7')) and not any(part in header for part in (b'INTERP', b'DYNAMIC')),
         'Not a static ARMv7 EABI5 hard-float program')
    for args in ((), ('-cpu', 'cortex-a7,neon=off,vfp-d32=off')):
        version = run('qemu-arm', *args, program, '--version')
        need(version.stdout == b'aidoku 0.1.3\n' and not version.stderr, 'Unexpected version or CLI noise')
    options = []
    for design in ('scribe', 'pw6'):
        result = run('qemu-arm', program, '--kindle', '--device', design, '--print-startup-options')
        need(result.stdout.strip() and not result.stderr, f'{design}: CLI options failed')
        options.append(result.stdout)
    return options


def verify(project):
    program = project / 'programs/candidate'
    cli(program, True)
    deps = project / 'target' / TARGET / 'release/deps'
    app = unique(deps, 'libaidoku_kindle-*.rlib')
    link = unique(Path(os.environ['HOME']) / '.cache/cargo-zigbuild', f'*/wrappers/*/zigcc-{TARGET}-*.sh')
    fingerprint = project / 'target' / TARGET / 'release/.fingerprint'
    native = [path for path in sorted(deps.glob('libslint_backend_kindle-*.rlib')) if
              'startup-draw-diagnostics' in json.loads((fingerprint /
               ('slint-backend-kindle-' + path.stem.split('-')[-1]) / 'lib-slint_backend_kindle.json').read_text())['features']]
    need(len(native) == 1, 'Expected only one feature-enabled vendor library')
    probe = project / 'probe/draw-probe'
    run('rustc', '--edition=2024', '--target', TARGET, '-C', 'opt-level=2', '-C', f'linker={link}',
        '--extern', f'aidoku_kindle={app}', '--extern', f'slint_backend_kindle={native[0]}',
        '-L', f'dependency={deps}', '-L', f'dependency={project}/target/release/deps', project / 'draw_probe.rs', '-o', probe)
    cases = []
    for case in CASES:
        result = run('qemu-arm', probe, case)
        lines = result.stderr.decode('ascii').splitlines()
        expect = case not in ('missing-frame', 'bad-marker', 'initial')
        need(not result.stdout and len(lines) == (4 if expect else 3), f'{case}: invalid number of records')
        expected = ['startup_library_submitted', 'startup_stage_diag', 'startup_transition'] + (['startup_draw_cpu'] if expect else [])
        need([line.split()[1] for line in lines] == expected and all(line.startswith('aidoku: ') for line in lines), f'{case}: event order')
        primary, stages, transition = map(fields, lines[:3])
        need(primary['boottime_us'] == stages['submitted_us'] == transition['submitted_us'], f'{case}: prior stamp mismatch')
        if expect:
            draw = fields(lines[3]); scribe = case == 'scribe'
            need(tuple(draw) == KEYS and len(lines[3].split()) == len(KEYS) + 2, f'{case}: schema')
            need(draw['submitted_us'] == primary['boottime_us'] and re.fullmatch('[1-9][0-9]*', draw['submitted_us']) and
                 draw['sequence'] == primary['sequence'] == transition['sequence'] == '4' and
                 draw['marker'] == primary['marker'] == transition['marker'] == '5' and
                 draw['wall_us'] == transition['render_us'] == '19', f'{case}: frame identity or wall mismatch')
            need((draw['width'], draw['height'], draw['format']) == (('1860', '2480', 'gray8') if scribe else ('1264', '1680', 'rgb8')),
                 f'{case}: wrong retained surface')
            need(draw['thread_us'] == ('none' if case in ('cpu-missing', 'both-missing') else '20'), f'{case}: CPU')
            counters = [draw[key] for key in ('minflt', 'majflt', 'nvcsw', 'nivcsw')]
            need(counters == (['none'] * 4 if case in ('counters-missing', 'both-missing') else ['0', '1', '2', '3']), f'{case}: deltas')
        cases.append({'case': case, 'event_count': len(lines), 'draw_line': lines[3] if expect else None})
    return {'scope': 'ARM/QEMU CLI + source-linked event formatting with supplied inputs; no device sample',
            'bytes': program.stat().st_size, 'two_cli_profiles': ['scribe', 'pw6'], 'cases': cases}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(verify(parser.parse_args().project.resolve()), indent=2))
