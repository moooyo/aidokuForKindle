"""Issue build provenance for a launcher-only change with unchanged R2 native programs."""

import argparse
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path('/root/aidoku-all-build-20261119')
ORIGIN = 'final-v2'
LAUNCH = 'packaging/kindle/launch.sh'
R2 = {
    'builder': '8d6d97ae4faa02f83a7e8462a2ff412b224bc6c2291a8a085b19e506a0e07051',
    'receipt': '8fe4362b2b0e3d12ac51047d22740cd9deae051b7a5431aae471ab6b8cf8b786',
    'source': '28c1bb314e12fe5689b401ca23cf932a91c138f5763c4c1ea18d454f41c990cf',
    'full': '4f4c0a487ba9391aa64551fefc8bcc17b0e646ada09bf83882234f0307330343',
    'app': '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af',
    'helper': 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50',
    'launcher': '2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5',
}
# This exact CPython file appeared after R2 was built. It was never in the R2
# archive. Audit and report it, without deleting it or treating it as an input.
POST_BUILD_BYTECODE = ('packaging/tests/__pycache__/launcher_test.cpython-313.pyc',
                       'eb7a3c3894b391aff6b42405fd00c8476afff77477723168c9e1e4233a9cec23')


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_bytes(data):
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def read(path):
    return json.loads(path.read_bytes())


def file(path, expected=None, mode=None):
    s = path.lstat()
    need(stat.S_ISREG(s.st_mode) and path == path.resolve(strict=True), 'Expected a direct regular file: ' + str(path))
    if mode is not None:
        need(stat.S_IMODE(s.st_mode) == mode, 'Unexpected file mode: ' + path.name)
    digest = sha(path)
    need(expected is None or digest == expected, 'Content mismatch: ' + path.name)
    return digest


def ref(path):
    return {'uri': path.as_uri(), 'sha256': file(path)}


def compare_trees(old, new):
    need(old.keys() == new.keys(), 'Frozen source adds or removes a path')
    changed = []
    for path in sorted(old):
        a, b = old[path], new[path]
        need(a[:2] == b[:2], 'File type or mode changed: ' + path)
        if a != b:
            changed.append(path)
    need(changed == [LAUNCH], 'Expected only a launcher content change; observed ' + str(changed))


def tree(root):
    rows = {}
    for p in (root, *root.rglob('*')):
        s = p.lstat(); name = p.relative_to(root).as_posix(); mode = stat.S_IMODE(s.st_mode)
        if stat.S_ISDIR(s.st_mode):
            rows[name] = ('directory', mode)
        else:
            need(stat.S_ISREG(s.st_mode), 'Unexpected link or special input: ' + name)
            rows[name] = ('file', mode, s.st_size, file(p))
    return rows


def archive_index(path, expected):
    file(path, expected)
    rows = {}
    with tarfile.open(path, 'r:*') as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            if name == PurePosixPath('.') and item.isdir():
                continue
            need(not name.is_absolute() and '..' not in name.parts and str(name) not in rows,
                 'Unsafe or duplicate source archive entry')
            mode = stat.S_IMODE(item.mode)
            if item.isdir():
                rows[str(name)] = ('directory', mode)
            else:
                need(item.isreg(), 'Source archive contains a link or special file')
                with archive.extractfile(item) as encoded:
                    digest = hashlib.file_digest(encoded, 'sha256').hexdigest()
                rows[str(name)] = ('file', mode, item.size, digest)
    return rows


def agree_with_archive(frozen, archive):
    files = {k:v[2:] for k,v in frozen.items() if v[0] == 'file'}
    need(files == {k:v[2:] for k,v in archive.items() if v[0]=='file'}, 'Archive does not reproduce the frozen files')


def measured_source(kit, source, reports, frozen):
    stored = read(reports/'build-inputs.json'); source_manifest = read(reports/'source-manifest.json')
    need(frozen['label'] == ORIGIN and frozen['all_build_and_test_inputs_sha256'] == R2['full'] and
         stored['sha256'] == kit['canonical'](stored['files']) == R2['full'], 'Unqualified original file list')
    need(kit['product'](source) == source_manifest and source_manifest['sha256'] == R2['source'] and
         all(file(ROOT/'tools'/k,s) for k,s in frozen['builder_sha256'].items()) and
         {k:file(source/k) for k in kit['PACK']} == frozen['packaging_sha256'], 'R2 source or build tooling changed')
    index = tree(source); observations = []
    path, digest = POST_BUILD_BYTECODE
    if path in index:
        need(index[path][0]=='file' and index[path][3]==digest, 'Unidentified host-generated file in the origin tree')
        observations.append({'path':path,'sha256':digest,'included_in_frozen_source':False});del index[path]
        need(index.pop(str(PurePosixPath(path).parent))[0]=='directory', 'Invalid bytecode directory')
    expected = {r['path']:(r['bytes'],r['sha256']) for r in stored['files']}
    need(expected == {k:v[2:] for k,v in index.items() if v[0]=='file'}, 'A frozen R2 input or unrelated extra file differs')
    return index, observations


def original_build(kit):
    source, reports, artifacts = kit['locations'](ORIGIN); frozen = read(reports/'freeze.json')
    index, observations = measured_source(kit,source,reports,frozen)
    receipt = reports / 'build-receipt.json'
    file(receipt, R2['receipt'])
    need(frozen['source_manifest_sha256'] == R2['source'] and frozen['all_build_and_test_inputs_sha256'] == R2['full']
         and frozen['source_file_count'] == 369, 'Origin is not the measured R2 build')
    checked = read(receipt)
    for item in checked['evidence']:
        need(item['uri'].startswith(ROOT.as_uri() + '/'), 'Unrecognized R2 build evidence')
        file(Path(item['uri'].removeprefix('file://')), item['sha256'])
    arm = reports / 'arm'
    result = read(arm / 'result.json')
    need(result.get('passed') is True and result['mode']=='arm' and result['source_manifest_sha256']==R2['source']
         and result['input_sha256']==R2['full'] and result['cargo_jobs']==6
         and not result['release_lto'] and result['release_codegen_units']==16, 'Unqualified original build')
    events = [json.loads(s) for s in (arm / 'cargo.jsonl').read_text().splitlines()]
    path = ROOT / 'target/armv7-unknown-linux-musleabihf/release/aidoku'
    selected = [e for e in events if e.get('reason')=='compiler-artifact' and e.get('executable')==str(path) and
                e.get('package_id')=='path+'+source.as_uri()+'#aidoku-kindle@0.1.3' and
                e.get('target',{}).get('kind')==['bin'] and e['target'].get('name')=='aidoku']
    need(len(selected)==1 and selected[0].get('fresh') is False and selected[0]['profile']['test'] is False
         and events[-1].get('reason')=='build-finished' and events[-1].get('success') is True,
         'Missing the original fresh ARM compiler event')
    # This is the actual backing object after the build namespace was unmounted.
    file(ROOT / 'upper/armv7-unknown-linux-musleabihf/release/aidoku', R2['app'])
    file(arm / 'aidoku-startup-portrait', R2['helper'])
    for role, name in [('app','candidate-arm'),('helper','aidoku-startup-portrait'),('launcher','launch.sh')]:
        file(artifacts/name, R2[role], 0o755)
    return source, reports, artifacts, frozen, index, observations


def prepare(label, origin, source_archive, check_only):
    need(sys.platform == 'linux' and origin == ORIGIN and label != ORIGIN, 'Only final-v2 may supply native programs')
    kit_path = ROOT / 'tools/integration.py'; file(kit_path, R2['builder'])
    kit = runpy.run_path(str(kit_path)); old, report0, artifacts0, prev, a, observed = original_build(kit)
    new, reports, artifacts, frozen = kit['verified'](label)
    need(frozen['source_manifest_sha256'] == prev['source_manifest_sha256'] and
         frozen['source_file_count'] == prev['source_file_count'] and
         read(reports/'source-manifest.json') == read(report0/'source-manifest.json'),
         'The 369 production inputs do not match R2')
    b = tree(new); compare_trees(a, b)
    # freeze normalizes permissions; check the original transport archive modes too.
    old_tar=ROOT/'incoming'/f'{ORIGIN}.tar'; new_tar=source_archive or ROOT/'incoming'/f'{label}.tar'
    need(new_tar.parent == ROOT/'incoming', 'Unrecognized incoming archive')
    ar, br = archive_index(old_tar, prev['source_archive_sha256']), archive_index(new_tar, frozen['source_archive_sha256'])
    compare_trees(ar, br); agree_with_archive(a,ar); agree_with_archive(b,br)
    need(frozen['all_build_and_test_inputs_sha256'] != R2['full'], 'Launcher change not present in the full input manifest')
    before, after = frozen['packaging_sha256'], prev['packaging_sha256']
    need(before.keys()==after.keys() and {k for k in before if before[k]!=after[k]}=={LAUNCH}, 'Other package tooling changed')
    need(all(frozen[k] == prev[k] for k in ('baseline_source_revision','baseline_source_sha256')), 'Different source ancestry')
    subprocess.run(['sh','-n',str(new/LAUNCH)], check=True)
    change=''.join(difflib.unified_diff((old/LAUNCH).read_text().splitlines(True), (new/LAUNCH).read_text().splitlines(True),
                                         fromfile='a/'+LAUNCH, tofile='b/'+LAUNCH)).encode()
    need(change, 'No represented script difference')
    proof={'mode':'reuse-programs', 'origin':origin,'label':label, 'source_manifest_sha256':R2['source'],
           'origin_all_inputs_sha256':R2['full'],'all_inputs_sha256':frozen['all_build_and_test_inputs_sha256'],
           'origin_archive_sha256':prev['source_archive_sha256'], 'archive_sha256':frozen['source_archive_sha256'],
           'canonical_production_input_count':369, 'all_files':sum(v[0]=='file' for v in b.values()),
           'archive_entries':len(br),'source_tree_modes_match':True,'archive_modes_match':True,
           'post_build_origin_observations':observed,
           'changed_paths':[LAUNCH], 'review_diff_sha256':hashlib.sha256(change).hexdigest(),
           'reused_arm_sha256':R2['app'],'reused_helper_sha256':R2['helper'],
           'original_fresh_cargo_event_verified':True,'origin_launcher_sha256':R2['launcher'],
           'launcher_sha256':b[LAUNCH][3], 'new_compilations':0,
           'scope':'Byte provenance only. R2 performance, default policy and device acceptance are not transferred.'}
    if check_only:
        print(json.dumps(proof, sort_keys=True));return
    need(not any(artifacts.iterdir()) and not (reports/'build-receipt.json').exists() and
         not (reports/'reuse').exists(), 'New label already has artifacts or reuse evidence; do not overwrite')
    with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='reuse-') as name:
        stage=Path(name); unpacked=stage/'programs'; unpacked.mkdir(); evidence=stage/'reuse';evidence.mkdir()
        members={}
        for key, leaf in kit['MEMBERS'].items():
            src = new/LAUNCH if leaf=='launch.sh' else artifacts0/leaf
            expected = b[LAUNCH][3] if leaf=='launch.sh' else R2['app' if leaf=='candidate-arm' else 'helper']
            target=unpacked/leaf;shutil.copyfile(src,target);target.chmod(0o755); members[key]=file(target,expected,0o755)
        (evidence/'launcher.patch').write_bytes(change);(evidence/'identities.json').write_bytes(json_bytes(proof))
        # Move the checked files first. The receipt is written last and is the admission boundary.
        evidence.rename(reports/'reuse')
        for item in unpacked.iterdir(): item.rename(artifacts/item.name)
        links=[report0/'build-receipt.json',report0/'arm/cargo.jsonl',report0/'arm/result.json',report0/'arm/aidoku-startup-portrait',
               reports/'freeze.json',reports/'source-manifest.json',report0/'build-inputs.json',reports/'build-inputs.json',
               reports/'reuse/launcher.patch',reports/'reuse/identities.json',Path(__file__).resolve(),
               *(artifacts/n for n in kit['MEMBERS'].values())]
        record={'baseline_source_revision':frozen['baseline_source_revision'],'baseline_source_sha256':frozen['baseline_source_sha256'],
                'source_manifest_sha256':frozen['source_manifest_sha256'],'program_sha256':members,
                'packaging_sha256':frozen['packaging_sha256'],'evidence':[ref(p) for p in links]}
        with (reports/'build-receipt.json').open('xb') as out:out.write(json_bytes(record))
    print(json.dumps({'receipt':str(reports/'build-receipt.json'),'receipt_sha256':sha(reports/'build-receipt.json'),
                      'program_sha256':members,'new_compilations':0}))


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('label');p.add_argument('--from',dest='origin',required=True)
    p.add_argument('--archive',type=Path);p.add_argument('--check-only',action='store_true');args=p.parse_args()
    os.umask(0o077)
    with (ROOT/'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(args.label,args.origin,args.archive,args.check_only)


if __name__ == '__main__':
    main()
