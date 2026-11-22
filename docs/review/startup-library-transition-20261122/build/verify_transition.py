"""Run the diagnostic ARM emitter under QEMU with controlled frame and lifecycle inputs."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


TARGET = 'armv7-unknown-linux-musleabihf'
STAMPS = ('fence_us', 'metadata_ready_us', 'metadata_received_us', 'fonts_ready_us', 'fonts_received_us',
          'fonts_registered_us', 'attach_enter_us', 'attach_exit_us', 'view_enter_us', 'view_exit_us',
          'scene_requested_us')
COSTS = ('render_us', 'settle_us', 'write_us', 'send_us')
CASES = ('scribe', 'pw6-empty', 'no-initial', 'wrong-fence', 'unavailable', 'no-main')


def command(*args):
    return subprocess.run(args, capture_output=True, check=True, timeout=90)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def exactly_one(directory, pattern):
    candidates = list(directory.glob(pattern))
    need(len(candidates) == 1, f'Expected one {pattern}')
    return candidates[0]


def parse_line(line, event, fields):
    tokens = line.decode('ascii').removesuffix('\n').split(' ')
    need(line.endswith(b'\n') and tokens[:2] == ['aidoku:', event], f'Invalid {event} line')
    values = [token.split('=', 1) for token in tokens[2:]]
    need(all(len(value) == 2 for value in values) and tuple(value[0] for value in values) == fields,
         f'Invalid {event} field order or count')
    return dict(values)


def stamp(value):
    need(re.fullmatch('[1-9][0-9]*', value), 'A specified timestamp must be a positive integer')
    return int(value)


def check_case(probe, case):
    output = command('qemu-arm', probe, case)
    lines = output.stderr.splitlines(keepends=True)
    need(not output.stdout and len(lines) == (1 if case == 'no-main' else 3), f'{case}: wrong line count')
    primary = parse_line(lines[0], 'startup_library_submitted',
                         ('boottime_us', 'sequence', 'marker', 'layout', 'library', 'grid'))
    layout, shelf = ('compact', 'empty') if case in ('pw6-empty', 'unavailable') else ('large', 'nonempty')
    need((primary['layout'], primary['library'], primary['grid']) == (layout, shelf, shelf), f'{case}: shelf')
    need((primary['sequence'], primary['marker']) == ('4', '5'), f'{case}: Library frame identity')
    submitted = stamp(primary['boottime_us'])
    if case != 'no-main':
        coarse = parse_line(lines[1], 'startup_stage_diag', ('main_us', 'initial_us', 'submitted_us'))
        fine = parse_line(lines[2], 'startup_transition', ('sequence', 'marker', 'submitted_us', *STAMPS, *COSTS))
        need(primary['boottime_us'] == coarse['submitted_us'] == fine['submitted_us'] and
             primary['sequence'] == fine['sequence'] and primary['marker'] == fine['marker'], f'{case}: Q/K/S')
        main = stamp(coarse['main_us'])
        need(main <= submitted, f'{case}: main order')
        if case == 'no-initial':
            need(coarse['initial_us'] == 'none', f'{case}: filled missing initial')
        else:
            need(main <= stamp(coarse['initial_us']) <= submitted, f'{case}: initial order')
        if case in ('no-initial', 'wrong-fence'):
            need(fine['fence_us'] == 'none', f'{case}: accepted an unrelated fence')
        else:
            need(stamp(coarse['initial_us']) <= stamp(fine['fence_us']) <= submitted, f'{case}: fence order')
        if case == 'unavailable':
            need(all(fine[name] == 'none' for name in (*STAMPS[1:], *COSTS)), f'{case}: invented timing')
        else:
            times = [stamp(fine[name]) for name in STAMPS[1:]]
            need(times == sorted(times) and main <= times[0] and times[-1] <= submitted, f'{case}: stage order')
            need([fine[name] for name in COSTS] == ['19', '0', '3', '2'], f'{case}: frame-interval substitution')
    return {'case': case, 'layout': layout, 'shelf': shelf, 'records': [line.decode().rstrip() for line in lines]}


def verify(root):
    program = root / 'programs/candidate'
    header = command('readelf', '-h', '-l', '-A', program).stdout
    required = (b'ELF32', b'little endian', b'Machine:                           ARM', b'Version5 EABI',
                b'hard-float ABI', b'Tag_CPU_arch: v7')
    need(all(token in header for token in required) and b'INTERP' not in header and b'DYNAMIC' not in header,
         'Expected a static ARMv7 hard-float application')
    binary = program.read_bytes()
    need(all(prefix in binary for prefix in [b'aidoku: startup_library_submitted boottime_us=',
                                          b'aidoku: startup_stage_diag main_us=', b'aidoku: startup_transition sequence=']),
         'Application lacks an expected log prefix')
    version = command('qemu-arm', program, '--version')
    portable = command('qemu-arm', '-cpu', 'cortex-a7,neon=off,vfp-d32=off', program, '--version')
    need(version.stdout == portable.stdout == b'aidoku 0.1.3\n' and not (version.stderr or portable.stderr),
         'Unexpected version or CLI diagnostic noise')
    for profile in ('scribe', 'pw6'):
        options = command('qemu-arm', program, '--kindle', '--device', profile, '--print-startup-options')
        need(options.stdout.strip() and not options.stderr, f'{profile}: options failed')
    deps = root / 'target' / TARGET / 'release/deps'
    lib = exactly_one(deps, 'libaidoku_kindle-*.rlib')
    linker = exactly_one(Path(os.environ['HOME']) / '.cache/cargo-zigbuild', f'*/wrappers/*/zigcc-{TARGET}-*.sh')
    probe = root / 'probe/transition-probe'
    probe.parent.mkdir(exist_ok=True)
    command('rustc', '--edition=2024', '--target', TARGET, '-C', 'opt-level=2', '-C', f'linker={linker}',
            '--extern', f'aidoku_kindle={lib}', '-L', f'dependency={deps}', '-L', f'dependency={root}/target/release/deps',
            root / 'transition_probe.rs', '-o', probe)
    return {'scope': 'ARM QEMU user-mode CLI and production emitter with controlled callback inputs; not device timings',
            'bytes': program.stat().st_size, 'elf': 'static ARMv7 EABI5 hard-float', 'version': version.stdout.decode().strip(),
            'no_neon_cli': True, 'profiles': ['scribe', 'pw6'], 'supplied_frame_costs_us': [19, 0, 3, 2],
            'cases': [check_case(probe, case) for case in CASES]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(verify(parser.parse_args().project.resolve()), indent=2))
