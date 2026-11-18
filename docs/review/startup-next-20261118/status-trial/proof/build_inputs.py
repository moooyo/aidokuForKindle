"""Bind an artifact to the observed compiler event and unchanged source snapshot."""

import hashlib
import json
from pathlib import Path
import runpy
import shutil
import sys


BASE = Path('/root/aidoku-next-promotion-20261118/cold-release-inputs/source')
COLD = '73f360f06b112c7f34a27b6f2ff90e5237d2146b9444aff0871bfcbafcea4549'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def source_file(root, out):
    verifier = runpy.run_path(str(BASE / 'scripts/verify-kindle-package.py'))
    before = verifier['source_manifest'](BASE)
    if before['sha256'] != COLD:
        raise ValueError('The verified cold baseline source changed')
    now = verifier['source_manifest'](root)
    old = {x['path']: x['sha256'] for x in before['files']}
    new = {x['path']: x['sha256'] for x in now['files']}
    changes = {k: {'baseline_sha256': old.get(k), 'candidate_sha256': new.get(k)}
               for k in sorted(old.keys() | new.keys()) if old.get(k) != new.get(k)}
    write(out, {'cold_source_manifest_sha256': COLD, 'source_manifest_sha256': now['sha256'],
                'source_file_count': now['file_count'], 'changed_sources': changes})


def executable(log, kind, is_test):
    candidates = []
    for line in log.read_text().splitlines():
        e = json.loads(line)
        if (e.get('reason') != 'compiler-artifact' or not e.get('executable')
                or not e['package_id'].endswith('#aidoku-kindle@0.1.3')
                or e['profile']['test'] is not is_test):
            continue
        if kind in e['target']['kind']:
            candidates.append(e)
    if len(candidates) != 1 or candidates[0]['fresh']:
        raise ValueError(f'Expected one fresh compilation event for {kind}, found {len(candidates)}')
    return Path(candidates[0]['executable'])


def main(argv):
    if argv[0] == '--test-executable':
        print(executable(Path(argv[1]), argv[2], True))
    elif argv[0] == '--record':
        out, mode, copy = Path(argv[1]), argv[2], Path(argv[3])
        built = executable(out / 'cargo.jsonl', 'bin', False)
        target = Path('/root/aidoku-next-status-20261118/target')
        expected = target / ('armv7-unknown-linux-musleabihf/release' if mode == 'arm' else 'release') / 'aidoku'
        if built.resolve() != expected:
            raise ValueError('Wrong executable path or target')
        shutil.copyfile(built, copy)
        copy.chmod(0o755)
        if sha(built) != sha(copy):
            raise ValueError('Staged copy mismatch')
        result = json.loads((out / 'source-before.json').read_text())
        result.update(artifact_sha256=sha(copy), artifact_bytes=copy.stat().st_size,
                      build_log_sha256=sha(out / 'build.log'),
                      compiler_artifact_event_log_sha256=sha(out / 'cargo.jsonl'),
                      build_kind=mode, cargo_jobs=6, release_codegen_units=16, release_lto=False)
        write(out / 'identity.json', result)
        print(json.dumps({'artifact': copy.name, 'sha256': result['artifact_sha256'],
                          'input_sha256': result['source_manifest_sha256']}))
    else:
        source_file(Path(argv[0]), Path(argv[1]))


if __name__ == '__main__':
    main(sys.argv[1:])
