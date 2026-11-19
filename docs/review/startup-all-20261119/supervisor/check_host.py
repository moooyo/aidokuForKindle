#!/usr/bin/env python3
"""Compare the frozen shell selector with the real Rust reader on test-env."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib


BUILD = Path('/root/aidoku-all-build-20261119')
SRC = BUILD / 'sources/final'
ART = BUILD / 'artifacts/final'
OLD = Path('/root/aidoku-next-supervisor-20261118/artifacts')
LAUNCH = 'packaging/kindle/launch.sh'
OPTIONS = 'src/startup_options.rs'
C_SOURCE = 'src/native_query/startup_portrait.c'
FAILURE_TEST = 'packaging/tests/startup_portrait_supervisor_test.py'
LIFECYCLE_TEST = 'packaging/tests/launcher_test.py'
PINS = {LAUNCH: '2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5',
        OPTIONS: '0b980f4a43bac07a1a934ad69792f18fffe3f0926fe48a10cee23803f5219406',
        C_SOURCE: '6d2135d1734b918de206544bfdf489137f8c97cb622be76271703f67b377fb45',
        FAILURE_TEST: '8d3fcaf7c2de90d5468d17d1facd9fbaae437c1f5a3af86c1021345b20caf01d',
        LIFECYCLE_TEST: '78323cfbe8fd397d8506affa5ff0ae0844bce63a4a2ff9eac3d660f4a6c1e47f'}
ARM_APP = '3ef03a4124970139fe46d3873c4b9fc7d5cd25af55e66482e497dca46b584375'
ARM_HELPER = 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
DEFAULTS = 'portrait=shell\nprewarm=0\nlast_cover_grace=off\nimport_thumbnail=idle\nfirst_layout=optimized\nresident=off\n'
NATIVE = b'portrait=native\n'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(check, text):
    if not check:
        raise ValueError(text)


def run(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, timeout=kw.pop('timeout', 8), **kw)


def rust_reader(tmp):
    # Compile an isolated host caller of the frozen module, pinning the two
    # dependencies to the production lockfile. Only measurement hooks are inert.
    lock = tomllib.loads((SRC / 'Cargo.lock').read_text())['package']
    versions = {k: next(p['version'] for p in lock if p['name'] == k) for k in ('anyhow', 'libc')}
    src = tmp / 'src'; src.mkdir()
    shutil.copyfile(SRC / OPTIONS, src / 'startup_options.rs')
    (tmp / 'Cargo.toml').write_text('[package]\nname="options_reader_probe"\nversion="0.0.0"\nedition="2024"\n[dependencies]\n'
                                + ''.join(f'{k}="={v}"\n' for k, v in versions.items()))
    (src / 'main.rs').write_text('''#![allow(dead_code)]
mod reader_timing { pub fn enabled() -> bool {false} pub fn mark(_: &str) {} }
mod startup_options;
fn main() {
    let path = std::env::args_os().nth(1).expect("data directory");
    print!("{}", startup_options::StartupOptions::load(std::path::Path::new(&path)));
}
''')
    cargo = shutil.which('cargo') or '/root/.cargo/bin/cargo'
    run([cargo, 'build', '--offline', '-j6'], cwd=tmp, timeout=150)
    return tmp / 'target/debug/options_reader_probe'


def regular_cases():
    return [(n, v, p) for n, v, p in (
        ('empty', b'', 'shell'), ('native', NATIVE, 'native'), ('shell', b'portrait=shell\n', 'shell'),
        ('all-six', b'resident=45\nfirst_layout=optimized\nlast_cover_grace=on\n' + NATIVE + b'import_thumbnail=idle\nprewarm=2\n', 'native'),
        ('only-other-settings', b'prewarm=2\nresident=45\n', 'shell'),
        ('unknown-key', b'portrait_extra=native\n', 'shell'), ('unknown-key-with-known', b'x=y\n' + NATIVE, 'native'),
        ('unknown-value', b'portrait=auto\n', 'shell'), ('blank-value', b'portrait=\n', 'shell'),
        ('extra-equals', b'portrait=native=shell\n', 'shell'), ('leading-space', b' portrait=native\n', 'shell'),
        ('trailing-space', b'portrait=native \n', 'shell'), ('case-sensitive', b'portrait=Native\n', 'shell'),
        ('malformed-unrelated-line', b'no-delimiter\n' + NATIVE, 'native'), ('final-no-lf', NATIVE[:-1], 'native'),
        ('duplicate-native', NATIVE + NATIVE, 'shell'), ('conflicting-duplicate', b'portrait=shell\n' + NATIVE, 'shell'),
        ('invalid-then-native', b'portrait=wrong\n' + NATIVE, 'shell'), ('three-portraits', NATIVE * 3, 'shell'),
        ('duplicate-other-key', b'prewarm=2\nprewarm=2\n' + NATIVE, 'native'),
        ('control-crlf', b'portrait=native\r\n', 'shell'), ('control-tab-after', NATIVE + b'foo=\t\n', 'shell'),
        ('nul-inline', b'portrait=na\x00tive\n', 'shell'), ('nul-after-valid', NATIVE + b'\x00', 'shell'),
        ('byte-del', NATIVE + b'\x7f', 'shell'), ('non-ascii', NATIVE + b'\xc3\xa9', 'shell'),
        ('boundary-1023', NATIVE + b'#' * (1023 - len(NATIVE)), 'native'),
        ('boundary-1024', NATIVE + b'#' * (1024 - len(NATIVE)), 'native'),
        ('boundary-1025', NATIVE + b'#' * (1025 - len(NATIVE)), 'shell'),
        ('late-duplicate-at-limit', b'x' * (1024 - 1 - len(NATIVE) * 2) + b'\n' + NATIVE * 2, 'shell'),
        ('inert-shell-text', b'portrait=$(printf native)\n', 'shell'),
    )]


def case_file(root, label, content):
    path = root / 'startup-options'
    if label == 'missing': return
    if label == 'directory': path.mkdir(); return
    if label == 'fifo': os.mkfifo(path); return
    if label in ('symlink', 'dangling-symlink'):
        if label == 'symlink': (root/'target').write_bytes(NATIVE)
        path.symlink_to(root/'target'); return
    path.write_bytes(content)


def select(root, raw, expected, override, shell_file, oracle):
    env = {k: v for k, v in os.environ.items() if k != 'AIDOKU_STARTUP_PORTRAIT'}
    if override is not None: env['AIDOKU_STARTUP_PORTRAIT'] = override
    rust = run([str(oracle), str(root)], env=env).stdout.decode('ascii')
    rows = rust.splitlines(); require(len(rows) == 6 and rows[0] == 'portrait=' + expected, 'Unexpected Rust choice')
    if raw is not None and (len(raw) > 1024 or any(b != 10 and not 32 <= b <= 126 for b in raw)):
        require(rust == DEFAULTS, 'Invalid bytes did not reset the complete Rust settings object')
    out = run(['/bin/sh', str(shell_file), str(root)], env=env).stdout
    # The Rust reader reports saved preferences. A diagnostic override affects only the launcher.
    effective = expected if override in (None, '') else ('native' if override == 'native' else 'shell')
    require(out == (effective + '\n').encode(), 'Unexpected shell selection: ' + effective)
    return {'saved_portrait_stdout': rows[0], 'selector_stdout': out.decode().strip()}


def full_entry(tmp, helper, value, override, helper_mode):
    spec = importlib.util.spec_from_file_location('release_launcher_fixture', SRC / LIFECYCLE_TEST)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    test = module.LauncherTests('test_exit_code_and_stock_repaint_are_preserved'); test.setUp()
    try:
        if value is not None: (test.home / 'data/startup-options').write_bytes(value)
        if helper_mode != 'absent':
            target = test.home / 'app/aidoku-startup-portrait'; shutil.copy2(helper, target)
            if helper_mode == 'nonexecutable': target.chmod(0o644)
        test.environment.pop('AIDOKU_STARTUP_PORTRAIT', None)
        if override is not None: test.environment['AIDOKU_STARTUP_PORTRAIT'] = override
        test.environment.update(AIDOKU_FAKE_EXIT_AFTER='0.4', AIDOKU_FAKE_EXIT_CODE='0')
        test.start_library_entry(); require(test.complete() == 0, 'Library entry failed'); test.assert_recovered()
        log = (test.home / 'data/logs/launcher.log').read_text()
        lines = re.findall(r'^aidoku: Startup portrait supervisor: (native|shell)\.$', log, flags=re.M)
        require(len(lines) == 1, 'Expected one actual launch engine record'); return lines[0]
    finally: test.tearDown()


def main(output):
    for path, digest in PINS.items(): require(sha(SRC / path) == digest, f'Unexpected frozen source: {path}')
    require(sha(ART/'launch.sh') == PINS[LAUNCH] and sha(ART/'candidate-arm') == ARM_APP and
            sha(ART/'aidoku-startup-portrait') == ARM_HELPER, 'Frozen product identity mismatch')
    shell = (SRC/LAUNCH).read_text()
    start = shell.index('startup_portrait_native() (\n'); stop = shell.index('\nnative_read_command() {', start)
    with tempfile.TemporaryDirectory(prefix='aidoku-portrait-options-') as name:
        tmp = Path(name); oracle = rust_reader(tmp)
        selector = tmp/'selector.sh'; selector.write_text(shell[start:stop] + '\nDATA_DIR=$1\nif startup_portrait_native; then echo native; else echo shell; fi\n')
        cases = regular_cases() + [(k, None, 'shell') for k in ('missing','symlink','dangling-symlink','directory','fifo')]
        table = []
        for label, blob, wanted in cases:
            directory = tmp / ('case-' + label); directory.mkdir(); case_file(directory,label,blob)
            table.append({'case':label, 'bytes': None if blob is None else len(blob), **select(directory,blob,wanted,None,selector,oracle)})
        overrides = []
        for file in ('native','shell','missing','nul-after-valid'):
            blob, wanted = next((b,w) for n,b,w in cases if n==file)
            for value in ('','native','shell','legacy','unknown','Native'):
                overrides.append({'file':file, 'override':value, **select(tmp/('case-'+file),blob,wanted,value,selector,oracle)})
        helper = tmp/'native-portrait.host'; run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(SRC/C_SOURCE),'-o',str(helper)])
        full = []
        for label, blob, ov, mode, wanted in [('default',None,None,'present','shell'),('native',NATIVE,None,'present','native'),
              ('explicit-shell',b'portrait=shell\n',None,'present','shell'),('legacy-overrides-native',NATIVE,'legacy','present','shell'),
              ('native-overrides-shell',b'portrait=shell\n','native','present','native'),('unknown-override',NATIVE,'unknown','present','shell'),
              ('missing-helper',NATIVE,None,'absent','shell'),('nonexecutable-helper',NATIVE,None,'nonexecutable','shell')]:
            got=full_entry(tmp,helper,blob,ov,mode); require(got==wanted,label); full.append({'case':label,'worker_log_engine':got,'native_state_restored':True})
        fault=run([sys.executable,'-I',str(SRC/FAILURE_TEST),'-v'],env=dict(os.environ,AIDOKU_TEST_PORTRAIT_SUPERVISOR=str(helper)),timeout=60)
        fault_text=fault.stderr.decode(); require(re.search(r'Ran 16 tests in [\d.]+s\n\nOK\s*$',fault_text) is not None,'Fault replay incomplete')
        fault_cases=re.findall(r'^(test_\w+) \(.*?\) \.\.\. ok$',fault_text,flags=re.M);require(len(fault_cases)==16,'Unexpected fault cases')
        result={'passed':True,'environment':'test-env, Linux /bin/sh and a host build of the exact Rust module',
                'scope':'Configuration parity for the portrait key, launcher-only diagnostic overrides, full Library waiter/worker with mocked services and app, native fault fixtures; no Kindle access or performance assertion.',
                'sha256':{'candidate_arm':ARM_APP,'portrait_arm':ARM_HELPER,**PINS,'check_host.py':sha(Path(__file__))},
                'file_cases':table,'overrides':overrides,'library_entry_cases':full,'native_fault_cases':fault_cases,
                'counts':{'file_cases':len(table),'override_cases':len(overrides),'library_entry_cases':len(full),'native_fault_cases':len(fault_cases)}}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2)+'\n');print(result['counts'])


if __name__ == '__main__':
    if sys.platform != 'linux': raise SystemExit('Run through ssh test-env')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
