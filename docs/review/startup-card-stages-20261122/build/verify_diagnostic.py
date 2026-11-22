"""Check one diagnostic ARM program and its source-linked timestamp emitter under QEMU."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


TARGET = 'armv7-unknown-linux-musleabihf'
CASES = {
    'scribe': ('large', 'nonempty', True),
    'pw6': ('compact', 'empty', True),
    'scribe-no-initial': ('large', 'nonempty', False),
}
PRIMARY = re.compile(rb'aidoku: startup_library_submitted boottime_us=([1-9][0-9]*) sequence=4 marker=5 '
                     rb'layout=(large|compact) library=(nonempty|empty) grid=(nonempty|empty)\n')
STAGES = re.compile(rb'aidoku: startup_stage_diag main_us=([1-9][0-9]*) initial_us=([1-9][0-9]*|none) '
                    rb'submitted_us=([1-9][0-9]*)\n')


def command(*args):
    return subprocess.run(args, capture_output=True, check=True, timeout=90)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def one(directory, pattern):
    found = list(directory.glob(pattern))
    need(len(found) == 1, f'Expected one {pattern}')
    return found[0]


def verify(project):
    program = project / 'programs/candidate'
    header = command('readelf', '-h', '-l', '-A', program).stdout
    required = (b'ELF32', b'little endian', b'Machine:                           ARM', b'Version5 EABI',
                b'hard-float ABI', b'Tag_CPU_arch: v7')
    need(all(part in header for part in required) and b'INTERP' not in header and b'DYNAMIC' not in header,
         'Not a static ARMv7 hard-float program')
    data = program.read_bytes()
    need(b'aidoku: startup_library_submitted boottime_us=' in data and b'aidoku: startup_stage_diag main_us=' in data,
         'Expected both log prefixes in the diagnostic program')
    version = command('qemu-arm', program, '--version')
    simple_cpu = command('qemu-arm', '-cpu', 'cortex-a7,neon=off,vfp-d32=off', program, '--version')
    need(version.stdout == simple_cpu.stdout == b'aidoku 0.1.3\n' and not (version.stderr or simple_cpu.stderr),
         'Unexpected CLI output or diagnostic noise')
    for profile in ('scribe', 'pw6'):
        options = command('qemu-arm', program, '--kindle', '--device', profile, '--print-startup-options')
        need(options.stdout.strip() and not options.stderr, f'{profile} startup options failed')

    deps = project / 'target' / TARGET / 'release/deps'
    lib = one(deps, 'libaidoku_kindle-*.rlib')
    linker = one(Path(os.environ['HOME']) / '.cache/cargo-zigbuild', f'*/wrappers/*/zigcc-{TARGET}-*.sh')
    probe = project / 'probe/diagnostic-probe'
    probe.parent.mkdir(exist_ok=True)
    command('rustc', '--edition=2024', '--target', TARGET, '-C', 'opt-level=2', '-C', f'linker={linker}',
            '--extern', f'aidoku_kindle={lib}', '-L', f'dependency={deps}',
            '-L', f'dependency={project}/target/release/deps', project / 'diagnostic_probe.rs', '-o', probe)
    results = []
    for case, (layout, shelf, has_initial) in CASES.items():
        result = command('qemu-arm', probe, case)
        lines = result.stderr.splitlines(keepends=True)
        need(not result.stdout and len(lines) == 2, f'{case}: expected exactly two stderr lines')
        primary, diagnostic = PRIMARY.fullmatch(lines[0]), STAGES.fullmatch(lines[1])
        need(primary and diagnostic, f'{case}: unexpected record format')
        sent, actual_layout, library, grid = primary.groups()
        main, initial, duplicate_sent = diagnostic.groups()
        need((actual_layout.decode(), library.decode(), grid.decode()) == (layout, shelf, shelf),
             f'{case}: profile or projected shelf mismatch')
        need(sent == duplicate_sent and int(main) <= int(sent), f'{case}: inconsistent boottime')
        need((initial != b'none') == has_initial, f'{case}: wrong optional first-full handling')
        if has_initial:
            need(int(main) <= int(initial) <= int(sent), f'{case}: out-of-order initial timestamp')
        results.append({'case': case, 'layout': layout, 'shelf': shelf, 'records': [line.decode().rstrip() for line in lines]})
    return {'scope': 'QEMU user-mode CLI and production timestamps with supplied frame checkpoints; not device timings',
            'elf_bytes': program.stat().st_size, 'elf': 'static ARMv7 EABI5 hard-float',
            'feature': 'startup-stage-diagnostics', 'qemu_version': version.stdout.decode().strip(),
            'no_neon_version': True, 'cli_profiles': ['scribe', 'pw6'], 'cases': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    print(json.dumps(verify(parser.parse_args().project.resolve()), indent=2))
