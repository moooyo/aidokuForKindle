"""Join the exact completed build, suite and pixel records for the selected source."""

import json
from pathlib import Path
import re
import runpy
import sys


ROOT = Path('/root/aidoku-next-status-20261118')
REPORTS = ROOT / 'reports'
INPUTS = runpy.run_path(str(Path(__file__).with_name('build_inputs.py')))
sha = INPUTS['sha']
write = INPUTS['write']


def bound(path):
    return {'uri': path.resolve(strict=True).as_uri(), 'sha256': sha(path)}


def released_build(kind, source_id):
    snapshots = sorted(REPORTS.glob(kind + '-*/source-after.json'))
    matching = [p for p in snapshots if json.loads(p.read_bytes())['source_manifest_sha256'] == source_id
                and p.read_bytes() == p.with_name('source-before.json').read_bytes()]
    if len(matching) != 1:
        raise ValueError(f'Expected one complete {kind} build for the selected final source')
    return matching[0].parent


def main(source_id):
    current = REPORTS / 'frozen-source.json'
    INPUTS['source_file'](ROOT / 'source', current)
    source = json.loads(current.read_bytes())
    if source['source_manifest_sha256'] != source_id or len(source['changed_sources']) != 5:
        raise ValueError('The product sources no longer match the proposed status candidate')
    dirs = {kind: released_build(kind, source_id) for kind in ('arm', 'host', 'desktop')}
    artifacts = {}
    for kind in ('arm', 'desktop'):
        info = json.loads((dirs[kind] / 'identity.json').read_bytes())
        artifact = ROOT / 'artifacts' / ('candidate-' + kind)
        if (info['artifact_sha256'] != sha(artifact) or info['source_manifest_sha256'] != source_id
                or info['compiler_artifact_event_log_sha256'] != sha(dirs[kind] / 'cargo.jsonl')
                or not info['artifact_bytes'] == artifact.stat().st_size):
            raise ValueError('Build evidence mismatch')
        artifacts[kind] = {'sha256': sha(artifact), 'bytes': artifact.stat().st_size}
    scores = {}
    for kind, minimum in [('lib', 824), ('bin', 22)]:
        log = dirs['host'] / (kind + '-tests.log')
        result = re.findall(r'^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;',
                            log.read_text(), re.MULTILINE)
        if len(result) != 1 or int(result[0][0]) < minimum or int(result[0][1]):
            raise ValueError('Missing successful test completion')
        scores[kind] = {k: int(v) for k, v in zip(('passed', 'failed', 'ignored'), result[0])}
    candidates = [(p, json.loads(p.read_bytes())) for p in REPORTS.glob('desktop-pixels-*/pixel-report.json')]
    captures = [(p, v) for p, v in candidates if v['desktop_elf_sha256'] == artifacts['desktop']['sha256']
                and not v['reference_only'] and v.get('passed')]
    if len(captures) != 1 or not all(c['full_image_identical'] for c in captures[0][1]['comparisons']):
        raise ValueError('Both profiles must match their qualified product baseline')
    conclusion = {**source, 'binaries': artifacts, 'host_suites': scores,
                  'toolchain': {'rustc': '1.99.1', 'zig': '0.15.2', 'cargo_jobs': 6,
                                'cargo_release_lto': False, 'cargo_release_codegen_units': 16},
                  'desktop_profiles': captures[0][1]['comparisons'],
                  'scope': 'Host build and regression qualification only; no physical startup performance inferred.'}
    write(REPORTS / 'build-and-pixels.json', conclusion)
    proofs = [current, dirs['arm'] / 'identity.json', dirs['arm'] / 'cargo.jsonl', dirs['arm'] / 'build.log',
              ROOT / 'upper/armv7-unknown-linux-musleabihf/release/aidoku',
              dirs['host'] / 'lib-tests.log', dirs['host'] / 'bin-tests.log',
              captures[0][0], REPORTS / 'build-and-pixels.json']
    write(REPORTS / 'build-attestation.json', {'source_manifest_sha256': source_id,
          'artifact_sha256': {'aidoku/app/aidoku': artifacts['arm']['sha256']},
          'evidence': [bound(p) for p in proofs]})
    print(json.dumps({'report_sha256': sha(REPORTS / 'build-and-pixels.json'),
                      'attestation_sha256': sha(REPORTS / 'build-attestation.json')}))


if __name__ == '__main__':
    main(sys.argv[1])
