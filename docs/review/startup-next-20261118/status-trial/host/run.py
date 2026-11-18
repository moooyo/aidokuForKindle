"""Build and exercise unchanged startup-toolbar and AQ1 modules on test-env."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path('/root/aidoku-next-status-20261118')
NATIVE = 'native_query.rs'
TOOLBAR = 'platform_startup_toolbar.rs'
CASES = ('ok', 'ok-offline', 'bad-hello', 'wrong-tag', 'short-reply', 'bad-wifi',
         'bad-battery', 'bad-exit', 'trailing', 'timeout', 'cancel', 'pre-cancel',
         'stale', 'sleep-before-send', 'sleep-before-view', 'sleep-after-view',
         'invalidate-before-view')
RUSTC = Path('/root/.cargo/bin/rustc')
DEPS = Path('/root/aidoku-y4-remaining-20261113-yg7wh3iy/target/release/deps')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cases(root):
    lower, upper, work = (root / n for n in ('system-lower', 'synthetic-upper', 'overlay-work'))
    for path in (lower, upper, work):
        path.mkdir(mode=0o700)
    synthetic = bytearray(52)
    synthetic[:7] = b'\x7fELF\x01\x01\x01'
    synthetic[16:18] = (3).to_bytes(2, 'little')
    synthetic[18:20] = (40).to_bytes(2, 'little')
    synthetic[36:40] = (0x400).to_bytes(4, 'little')
    (upper / 'liblipc.so.1').write_bytes(synthetic)
    # Private namespace only: real ELF discovery reads the fake header at the
    # canonical path. No replacement function, firmware library or rootfs write.
    subprocess.run(['mount', '--bind', '/usr/lib', str(lower)], check=True)
    subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o',
                    f'lowerdir={lower},upperdir={upper},workdir={work}', '/usr/lib'], check=True)
    if Path('/usr/lib/liblipc.so.1').read_bytes() != synthetic:
        raise ValueError('Synthetic header unavailable in private namespace')
    records = []
    for case in CASES:
        case_dir = root / ('case-' + case)
        case_dir.mkdir(mode=0o700)
        out = subprocess.run([str(root / 'probe')], cwd=case_dir, check=True, timeout=4,
                             env=dict(os.environ, FIXTURE_CASE=case), text=True, capture_output=True)
        name, opcodes, elapsed, marks = out.stdout.strip().split('|')
        if name != case:
            raise ValueError('Fixture reported the wrong case')
        records.append(dict(name=name, recorded_AQ1_properties=int(opcodes),
                            elapsed_ms=int(elapsed), marks=marks.split(',')))
    print(json.dumps(records))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if sys.platform != 'linux' or not ROOT.is_dir():
        parser.error('Run through ssh test-env')
    os.umask(0o077)
    if args.namespace:
        run_cases(args.namespace.resolve(strict=True))
        return
    if not args.output:
        parser.error('Supply --output')
    fixture = Path(__file__).with_name('probe.rs').resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='toolbar-host-fixture-', dir=ROOT / 'tmp') as tmp:
        stage = Path(tmp)
        bindings = {}
        for name in (NATIVE, TOOLBAR):
            source = ROOT / 'source/src' / name
            copied = Path(shutil.copyfile(source, stage / name))
            if sha(source) != sha(copied):
                raise ValueError('The fixture source differs from the production source')
            bindings[name] = sha(source)
        shutil.copyfile(fixture, stage / 'probe.rs')
        anyhow = DEPS / 'libanyhow-96633ffde56d7d3d.rlib'
        libc = DEPS / 'liblibc-83e43155457431f5.rlib'
        subprocess.run([str(RUSTC), '--edition=2024', '--cfg', 'feature="kindle"', '-C', 'panic=abort',
                        '-L', f'dependency={DEPS}', '--extern', f'anyhow={anyhow}', '--extern', f'libc={libc}',
                        str(stage / 'probe.rs'), '-o', str(stage / 'probe')], check=True)
        os.link(stage / 'probe', stage / 'aidoku-native-query')
        proc = subprocess.run(['unshare', '--mount', '--propagation', 'private', sys.executable, '-I',
                               str(Path(__file__).resolve()), '--namespace', str(stage)],
                              text=True, stdout=subprocess.PIPE, check=True, timeout=30)
        records = json.loads(proc.stdout)
        evidence = {'source_sha256': bindings, 'fixture_sha256': sha(fixture),
                    'runner_sha256': sha(Path(__file__)), 'passed': len(records), 'cases': records,
                    'production_source_changes': 0, 'environment': 'test-env, Linux x86_64 process and pipes',
                    'scope': 'The exact product modules drive real process spawning, AQ1 handshake and frames, EOF, exit, polling, consumed values, timeout, cancellation and reaping. A private mount exposes a synthetic 52-byte ABI header: the unmodified discovery function resolves the fake sibling executable. DeviceStatus and reader_timing are fixture adapters. Frame boundaries and fresh/expired instants are supplied by the harness. No framebuffer, native firmware service, device timing or hardware qualification is claimed.'}
        args.output.parent.mkdir(exist_ok=True, parents=True)
        args.output.write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps({'passed': len(records), 'source_sha256': bindings}))


if __name__ == '__main__':
    main()
