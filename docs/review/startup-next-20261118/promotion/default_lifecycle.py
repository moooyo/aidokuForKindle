#!/usr/bin/env python3
"""Freeze a default-options lifecycle qualification; no firmware service is modified.

Preparation is host-only. A generated runner reuses an exact previously reviewed
lease/transport/bridge implementation. Only its case set, preset run root and
per-case timeout differ. The shared document/dispatcher and indexed metadata
remain under the pre-existing source and native restoration guards.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import uuid


HERE = Path(__file__).resolve().parent
WARM = Path('/root/aidoku-next-bench-20261118/tools')
COLD = Path('/root/aidoku-next-coldbench-20261118/tools')
ROOT = Path('/root/aidoku-next-lifecycle-20261118')
DEPOT = Path('/root/aidoku-startup-delivery-20261118/artifacts/package-stage')
PIN = {
    'run_bench.py': '85d60c15b871375477cd14ef2020f0cc6adb7119ef618a010bec6ed934d97f7c',
    'bench_device.sh': '3126a4c5ceffb3836218f9f9ccb9dab2b111c67c56a0d380c98a8167379da14d',
    'cold-device': '49d597c9aae6348244b12e2b9a3345c2e60cb227fbab58ee3b7a3fe07f87c057',
}
CAPTURE = Path('/root/aidoku-native-query-20261117/tools/framebuffer-dump')
CAPTURE_SHA = 'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sha(content):
    return hashlib.sha256(content).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def bound(path, expected):
    check(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(), 'Invalid source')
    data = path.read_bytes()
    check(sha(data) == expected, 'Unexpected source identity: ' + path.name)
    return data


def once(source, old, new):
    check(source.count(old) == 1, 'Lifecycle qualification integration anchor differs')
    return source.replace(old, new, 1)


def runner_text():
    source = bound(WARM / 'run_bench.py', PIN['run_bench.py']).decode('ascii')
    source = once(source, str(WARM.parent), str(ROOT))
    source = once(source, "'run_case '+shlex.quote(case),timeout=80", "'run_case '+shlex.quote(case),timeout=155")
    source = once(source, "    if ns.action=='prepare':prepare(ns)",
                  "    if ns.action=='prepare': raise ValueError('Prepare with default_lifecycle.py')")
    return source.encode('ascii')


def bridge(run):
    check(len(run) <= 48 and run.startswith('aidoku-next-life-') and run.removeprefix('aidoku-next-life-').isalnum(),
          'Invalid lifecycle identity')
    return f'''#!/bin/sh
set -eu
test "$#" = 1 && test "$1" = --library || exit 64
stage=/mnt/us/.{run}
test "$(readlink -f "$stage")" = "$stage" && test "$(cat "$stage/owner")" = {run} || exit 73
case "$(cat "$stage/case")" in baseline-default) v=baseline;; candidate-default) v=candidate;; *) exit 64;; esac
export AIDOKU_HOME="$stage/$v"
exec sh "$AIDOKU_HOME/launch.sh" --library
'''.encode('ascii')


def device_text(cold):
    source = (bound(COLD / 'bench_device.sh', PIN['cold-device']) if cold
              else bound(WARM / 'bench_device.sh', PIN['bench_device.sh'])).decode('ascii')
    if not cold:
        source = once(source, '    cp -rp "$f" "$STAGE/data"', '    : # Each default case receives its own identical seed.')
    left = source.index('run_case() {'); right = source.index('restore_launcher() {', left)
    case = '''run_case() {
    case "$1" in baseline-default) v=baseline;; candidate-default) v=candidate;; *) return 1;; esac
    case_id=$1; OUT=$STAGE/out/$case_id
    owner; stable_entry; native_wait; same_hash "$INSTALLED" "$BRIDGE_SHA"
    [ ! -e "$OUT" ] && [ ! -L "$OUT" ]; mkdir "$OUT"
    [ ! -e "$STAGE/$v/data" ] && [ ! -L "$STAGE/$v/data" ]; cp -rp "$STAGE/fixture" "$STAGE/$v/data"
    (cd "$STAGE/$v/data" && sha256sum library.sqlite3 device-profile) >"$OUT/seed.sha256"
    (cd "$STAGE/fixture" && sha256sum library.sqlite3 device-profile) >"$OUT/expected-seed.sha256"
    cmp "$OUT/expected-seed.sha256" "$OUT/seed.sha256"
    # Unmodified programs receive their ordinary per-home data layout.
    (cd "$STAGE/$v/data" && find . -type f | LC_ALL=C sort | while IFS= read -r f; do
        sha256sum "$f" || exit 1
    done) >"$OUT/fixture-list.private"
    [ -s "$OUT/fixture-list.private" ]; digest "$OUT/fixture-list.private" >"$OUT/fixture-state.sha256"
    rm "$OUT/fixture-list.private"
    POLICY_CHECK
    printf '%s\\n' "$case_id" >"$STAGE/case.new"; mv "$STAGE/case.new" "$STAGE/case"
    export RUN STAGE OWNER_HASH BRIDGE_SHA ORIGINAL_SHA
    result=$(sh "$STAGE/lifecycle_device.sh" "$v" "$RUN"); test "$result" = LIFECYCLE_OK
    printf '%s\\n' "$result"
    stable_entry; native_wait; same_hash "$INSTALLED" "$BRIDGE_SHA"
}
'''
    policy = ('''test ! -e "$STAGE/$v/data/cache"; test ! -L "$STAGE/$v/data/cache"
    cmp "$STAGE/seed.sha256" "$OUT/seed.sha256"
    digest "$OUT/seed.sha256" >"$OUT/cold-fixture.sha256"
    printf 'empty-cache-v1\\n' >"$OUT/cold-fixture"
    printf 'empty-cache-v1\\n' >"$OUT/fixture-regime.txt"''' if cold else
              '''test -f "$STAGE/$v/data/cache/shared-v1/index.sqlite3"
    printf 'warm-cache-v1\\n' >"$OUT/fixture-regime.txt"''')
    source = source[:left] + case.replace('POLICY_CHECK', policy) + source[right:]
    # Recovery never signals a process on the strength of the path alone.
    left = source.index('    if [ -e "$LOCK" ]; then', source.index('restore_launcher() {'))
    right = source.index('    native_wait\n', left)
    source = source[:left] + '''    if [ -f "$STAGE/case" ]; then
        export RUN STAGE OWNER_HASH BRIDGE_SHA ORIGINAL_SHA
        result=$(sh "$STAGE/lifecycle_device.sh" recover "$RUN"); test "$result" = LIFECYCLE_RECOVERED
    else [ ! -e "$LOCK" ] && [ ! -L "$LOCK" ]; fi
''' + source[right:]
    return source.encode('ascii')


def freeze(args):
    gate = load('lifecycle_freeze_gate', HERE / 'prepare.py')
    selection_bytes = bound(args.selection, args.selection_sha256)
    selection = gate.unique_json(selection_bytes)
    candidates = selection['candidates']
    allowed = gate.allowed_changes(candidates, selection['artifacts'])
    check(selection['schema'] == 1, 'Unknown selection version')
    blobs = {name: gate.load_bound(selection['artifacts'][name]) for name in allowed}
    fingerprints = {name: sha(data) for name, data in blobs.items()}
    check(fingerprints.get(gate.APP) != 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6',
          'The earlier cold-only candidate failed its independent warm guard')
    seen = set(); refs = {}; status = 'status' in candidates; cold = 'cold' in candidates or status

    def evidence(label, reference):
        value = gate.load_bound(reference); refs[label] = value
        return gate.unique_json(value)

    def stage_pair(name, specs, picked, prefix, combined=False):
        reports = []
        for phase in ('screen', 'confirm'):
            record = evidence(prefix + phase, specs[phase]); reports.append(record)
            runs = gate.freeze_gate(record, phase, name, picked)
            gate.release_trial_context(record, picked, combined=combined)
            check(seen.isdisjoint(runs), 'Timing batches reused'); seen.update(runs)
        check(reports[0]['artifacts'] == reports[1]['artifacts'], 'Optimization changed between timing phases')
        return reports[1]['artifacts']

    for candidate, stage in candidates.items():
        name_prefix = '' if len(candidates) == 1 else candidate + '_'
        matched = stage_pair(candidate, stage, {k: fingerprints[k] for k in gate.MEMBERS[candidate]}, name_prefix)
    if status:
        warm = evidence('warm_guard', candidates['status']['warm_guard'])
        warm_ids = gate.warm_status_guard(warm, fingerprints)
        check(seen.isdisjoint(warm_ids), 'Warm control reused independent cold timing runs')
        seen.update(warm_ids)
    if len(candidates) > 1:
        combo = selection['combination']; workload = combo['primary']
        matched = stage_pair(workload, combo, fingerprints, '', True)
    else:
        check('combination' not in selection, 'Unnecessary combination selection')
        workload = next(iter(candidates))
    pixels = evidence('pixels', selection['qualification'])
    gate.bind_workload(pixels, workload); gate.bind_measurement(pixels, fingerprints)
    candidate_tuple = gate.DEPLOYED | fingerprints
    check(pixels.get('valid') is True and pixels.get('qualification_passed') is True and
          gate.observed_artifacts(pixels['artifacts']['candidate']) == candidate_tuple, 'No matching pixel qualification')
    ids = {b['run_id'] for b in pixels['batches'] if b['mode'] == 'qualify' and b['artifacts'] == pixels['artifacts']}
    check(ids and ids.isdisjoint(seen) and {e['batch'] for e in pixels['pixels']} == ids and all(
        e['identical_below_status_bar'] is True for e in pixels['pixels']), 'No independent picture qualification')
    if cold:
        check(pixels['artifacts'] == matched, 'Cold workload has different pixel program identities')
    if status:
        gate.qualify_status(pixels)
    source_root = args.source_root
    check(source_root.is_absolute() and source_root.resolve(strict=True) == source_root, 'Noncanonical source root')
    verifier = load('lifecycle_source_verifier', gate.checked_path(source_root / 'scripts/verify-kindle-package.py'))
    manifest = verifier.source_manifest(source_root)
    check(selection['source_manifest_sha256'] == manifest['sha256'], 'Candidate native sources differ from the selected snapshot')
    if status:
        check(manifest['sha256'] == '1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768',
              'The status and image changes do not match the independent combined build')
    attestation_raw = bound(args.build_attestation, args.build_attestation_sha256)
    attestation = gate.unique_json(attestation_raw)
    check(attestation.get('source_manifest_sha256') == manifest['sha256'] and attestation['artifact_sha256'] == fingerprints,
          'Build evidence does not bind selected artifacts or source')
    records = attestation.get('evidence', [])
    check(isinstance(records, list) and 0 < len(records) <= 16, 'No producer build evidence')
    for item in records:
        check(gate.load_bound(item), 'Original build evidence unavailable')
    run = 'aidoku-next-life-' + uuid.uuid4().hex[:8]
    cases = ['baseline-default', 'candidate-default']
    files = {}; variants = {}

    def add(name, data, mode=0o600):
        files[name] = (data, mode)

    add('owner', (run + '\n').encode()); add('capture', bound(CAPTURE, CAPTURE_SHA), 0o755)
    add('bridge.sh', bridge(run), 0o755)
    smoke = (HERE / 'smoke.sh').read_bytes(); anchor = b'# Entry point.'
    check(smoke.count(anchor) == 1, 'Smoke library boundary changed')
    add('smoke-lib.sh', smoke.split(anchor)[0]); add('bench-lib.sh', bound(WARM / 'bench_device.sh', PIN['bench_device.sh']))
    add('lifecycle_device.sh', gate.checked_path(HERE / 'lifecycle_device.sh').read_bytes(), 0o755)
    original = bound(DEPOT / gate.LAUNCH, gate.DEPLOYED[gate.LAUNCH]); source_launcher = source_root / 'packaging/kindle/launch.sh'
    check(sha(gate.checked_path(source_launcher).read_bytes()) == candidate_tuple[gate.LAUNCH], 'Source launcher differs from selected input')
    for side in ('baseline', 'candidate'):
        identities = gate.DEPLOYED if side == 'baseline' else candidate_tuple
        chosen = {} if side == 'baseline' else blobs
        app = identities[gate.APP]; key = 'installed' if app == gate.DEPLOYED[gate.APP] else app
        if key != 'installed':
            add('bin-' + key, chosen[gate.APP], 0o755)
        add(side + '/binary-hash', (app + '\n').encode()); add(side + '/binary-ref', (key + '\n').encode())
        add(side + '/launch.sh', chosen.get(gate.LAUNCH, original), 0o755)
        extra = {}
        if gate.EXTRA in chosen:
            base = Path(gate.EXTRA).name; add(side + '/app/' + base, chosen[gate.EXTRA], 0o755)
            extra[base] = identities[gate.EXTRA]
        spec = {'application_sha256': app, 'launcher_sha256': identities[gate.LAUNCH],
                'native_query_sha256': identities[gate.QUERY], 'args': [], 'env': {}, 'extra_sha256': extra}
        spec['variant_sha256'] = sha(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode())
        variants[side] = spec
    for name, (data, _) in files.items():
        if name.endswith('.sh'):
            subprocess.run(['sh', '-n'], input=data, check=True)
    add('inputs.sha256', ''.join(f'{sha(data)}  {name}\n' for name,(data,_) in files.items()).encode())
    runner, device = runner_text(), device_text(cold)
    subprocess.run(['sh', '-n'], input=device, check=True)
    batch = ROOT / 'private' / run; batch.mkdir(mode=0o700,parents=True)
    with tarfile.open(batch/'payload.tar.gz','w:gz',compresslevel=1) as archive:
        for name,(content,mode) in files.items():
            info=tarfile.TarInfo(name); info.size=len(content); info.mode=mode
            archive.addfile(info, io.BytesIO(content))
    (batch/'runner.py').write_bytes(runner); (batch/'device.sh').write_bytes(device)
    (batch/'original-launcher').write_bytes(original)
    (batch/'authoring-selection.json').write_bytes(selection_bytes); (batch/'build-attestation.json').write_bytes(attestation_raw)
    (batch/'qualified-source-manifest.json').write_text(json.dumps(dict(manifest,algorithm=verifier.SOURCE_ALGORITHM), indent=2)+'\n')
    for name, raw in refs.items():
        (batch/(name+'.json')).write_bytes(raw)
    plan = {'run_id':run,'mode':'default-lifecycle','primary':workload,'candidate_names':sorted(candidates),
            'cache_regime':'empty-cache-v1' if cold else 'warm-cache-v1',
            'fixture_policy':'per-case-empty-cache-v1' if cold else 'per-side-seeded-warm-cache-v1',
            'cases':cases,'variants':variants,'owner_hash':sha(files['owner'][0]),'bridge_sha256':sha(files['bridge.sh'][0]),
            'payload_sha256':sha((batch/'payload.tar.gz').read_bytes()),'device_script_sha256':sha(device),
            'runner_sha256':sha(runner),'original_launcher_sha256':sha(original),'selection_sha256':sha(selection_bytes),
            'reference_hashes':{k:sha(v) for k,v in refs.items()}, 'qualification_sha256':sha(refs['pixels']),
            'source_manifest_sha256':manifest['sha256'], 'source_manifest':{'source_sha256':manifest['sha256'],
            'build_attestation_sha256':sha(attestation_raw)}, 'program_sha256':fingerprints}
    gate.write_json(batch/'plan.json', plan)
    return batch/'plan.json'


def main():
    check(sys.platform == 'linux', 'Run on test-env')
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('selection','source-root','build-attestation'):
        parser.add_argument('--'+arg,type=Path,required=True)
    for arg in ('selection-sha256','build-attestation-sha256'):
        parser.add_argument('--'+arg,required=True)
    print(freeze(parser.parse_args()))


if __name__ == '__main__':
    main()
