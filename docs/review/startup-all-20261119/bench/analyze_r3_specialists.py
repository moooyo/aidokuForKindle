#!/usr/bin/env python3
"""Replay R3 reboot-free qualifications and the six-pair reopen screen offline."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_r3_specialists import ELF, HELPER, LAUNCH, RELEASE, ROOT, need, pin, sha

REOPEN = ('030149b2730fff376d32ded1fb25ce9f7f81337aed9a37faf77996d710d4fcb0',
          'c8bbdad79863a7785c3cfd41c07662dec011f60bb3c5971745be4a727e318b21')
WAKE = ('2dab3b868d07cfec73b950fd47cec08899011a39072e9bbeff1ad65424b900f5',
        'fe3271f41126d908c0db2281d5ab68f3e3191ca5b6432a67d9eaa6e4048abe1e')
IMPORT = ('9e33056ca86e11439070973b76bad56539828d1bbf695cb020bc94965fc2a118',
          '3c9bdfde3fc87385283d1070a37340e950c9d0a037ffd421037e67f0e3c7a7ad')


def load(name, path, expected):
    raw = pin(path, expected)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(raw, path, 'exec'), module.__dict__)
    return module


def inputs(path, expected, pair, option, control, candidate):
    path = path.resolve(strict=True)
    if path.name != 'plan.json':
        path /= 'plan.json'
    p = json.loads(pin(path, expected))
    root = path.parent
    need(p['run_id'] == root.name and (p['runner_sha256'], p['device_script_sha256']) == pair,
         'wrong_private_plan_or_runner')
    for arm, setting in [('baseline', control), ('candidate', candidate)]:
        v = p['variants'][arm]
        need(v['application_sha256'] == ELF and v['launcher_sha256'] == LAUNCH and
             v['extra_sha256'] == {'aidoku-startup-portrait': HELPER} and
             v['startup_options'] == {option: setting} and not v['args'] and not v['env'], 'wrong_r3_variant')
    mod = load('_r3_private_runner', root/'runner.py', pair[0])
    # Bind staged launchers to exact release bytes and the reviewed instrumentation,
    # not just to the claimed variant hashes in a report.
    with tarfile.open(root/'payload.tar.gz', 'r:gz') as archive:
        for arm in ('baseline', 'candidate'):
            need(archive.extractfile(f'{arm}/launch.sh').read() == mod.instrument(pin(RELEASE/'launch.sh', LAUNCH).decode(), []),
                 'different_launcher_in_private_payload')
            need(sha(archive.extractfile(f'{arm}/app/aidoku-startup-portrait').read()) == HELPER,
                 'different_helper_in_private_payload')
        need(sha(archive.extractfile('bin-'+ELF).read()) == ELF, 'different_elf_in_private_payload')
    return root, p


def wake_pixels(ref, root):
    from PIL import Image

    rows = {}
    for arm in ('baseline', 'candidate'):
        d = root/'outputs/out'/f'{arm}-qualify'/'second'
        need(ref.textfile(d/'wake-ok') == 'same-gui-returned\n', 'missing_wake_completion')
        need(ref.textfile(d/'wake-owner').splitlines() == [ref.textfile(d/'gui-owner').strip()]*2, 'wake_owner_mismatch')
        outstanding = [line.split() for line in ref.textfile(d/'wake-input.tsv').splitlines()]
        need([a for a, _ in outstanding] == ['active', 'parked', 'wake'], 'missing_wake_input_receipts')
        before, parked, actual = [int(n) for _, n in outstanding]
        need(0 <= parked < before == actual, 'input_release_or_reacquisition_unproven')
        need(ref.textfile(d/'wake-platform.tsv') == 'parked true true true\nwake false false false\n', 'wake_platform_not_restored')
        timestamps = [line.split() for line in ref.textfile(d/'wake-markers.tsv').splitlines()]
        need([k for _, k in timestamps] == ['ready_before_sleep', 'parked', 'active_after_wake'], 'missing_wake_markers')
        pre, sleep, wake = [ref.role_clock(t) for t, _ in timestamps]
        clock, _ = ref.audit_leg(d, False)
        need(clock.covers + 10_000 <= pre < sleep < wake < clock.home, 'invalid_wake_or_capture_order')
        pics = [d/f'{side}-wake.ppm' for side in ('pre', 'post')]
        with Image.open(pics[0]) as a, Image.open(pics[1]) as b:
            need(a.size == b.size == (1860, 2480) and a.mode == b.mode == 'RGB', 'wrong_wake_pixel_geometry')
            hashes = []
            for box in ((0, 72, 1860, 2480), (930, 0, 1860, 72)):
                ac, bc = a.crop(box), b.crop(box)
                lo, hi = ac.convert('L').getextrema()
                need(hi-lo > 80 and ac.tobytes() == bc.tobytes(), 'wake_library_or_stable_toolbar_pixels_differ')
                hashes.append(sha(ac.tobytes()))
        rows[arm] = {'same_second_gui': True, 'active_input_handles': before, 'parked_input_handles': parked,
                     'woken_input_handles': actual, 'visible_library_sha256': hashes[0], 'right_toolbar_sha256': hashes[1]}
    return rows


def reopen(scope, root, plan):
    ref = load('_r3_reopen_reviewed', ROOT/'reopen_probe/analyze_reopen.py', '247944b89e4454bafea66fdbd3beb93b79c8a4cf2175e597e7d402a953764005')
    is_wake = scope == 'wake'
    ref.RUNNER, ref.DEVICE = WAKE if is_wake else REOPEN
    phase = 'screen' if scope == 'screen' else 'qualify'
    scenario = scope if scope in ('ordinary', 'ttl') else 'immediate'
    need(plan['mode'] == phase and plan['reopen_scenario'] == scenario and
         plan.get('reopened_powerd_wake', False) is is_wake, 'wrong_reopen_scenario')
    need(json.loads((root/'report.json').read_bytes()).get('reopened_powerd_wake', False) is is_wake, 'wrong_reopen_report_scenario')
    out = ref.analyze([root] if phase == 'screen' else [], [root] if phase == 'qualify' else [], phase, ELF)
    if is_wake and out['valid']:
        out['short_sleep_visual'] = wake_pixels(ref, root)
    out['scope_name'] = scope
    out['qualified'] = out['valid'] and (out['latency_gate_passed'] if phase == 'screen' else out['qualification_passed'])
    return out


def import_qualification(root, plan):
    d = ROOT/'import_probe_r2/tools'
    for name, digest in {
        'attest.py': '41cbed24497802875621cf5f6662b8eeda9eab10e8e1fb4f04ad41241498e001',
        'fixture.py': '01414496efd2b5fd0aa929b3bdaeb4fefafccfc9437aab67dea2e07f42856fb1',
        'inventory_records.py': '58db96c2bc66b3cb71482f3a956896ef5b7f76f3e44ec6cdae70f387a5325f56',
        'materialize_guard.py': 'd615fc1ef1805532b3e1ea9fe9fad8cb47cf91e9b3943a9b5890e9544557f997',
    }.items():
        pin(d/name, digest)
    path = d/'analyze_import.py'
    source = pin(path, 'fe4039045cbb1cde54a3d18313e0eef31a6cfbddfa45158a4d62435b405311a6').decode()
    old = "need(not any(v[k] for k in ('env','args','extra_sha256')),'unrelated_options')"
    need(source.count(old) == 1, 'changed_import_analysis_anchor')
    source = source.replace(old, "need(not any(v[k] for k in ('env','args')),'unrelated_options')")
    env = {'__file__': str(path), '__name__': '_r3_import_reviewed'}
    sys.dont_write_bytecode = True
    exec(compile(source, path, 'exec'), env)
    pin(root/'device.sh', IMPORT[1]); pin(root/'runner.py', IMPORT[0])
    env['materialize'].generate = lambda: {key: (root/value).read_bytes().decode() for key, value in
                                           [('run_bench.py', 'runner.py'), ('bench_device.sh', 'device.sh')]}
    need(plan['mode'] == 'qualify', 'wrong_import_scope')
    result = env['verify_batch'](root, 'qualify')
    return {'scope_name': 'import', 'valid': True, 'qualified': True, 'performance_claim': False,
            'run_id': result['run_id'], 'artifacts': result['artifacts'], 'cases': result['rows'],
            'source_images_verified': True, 'paired_native_pixels_verified': True}


def main():
    if not ROOT.is_dir() or sys.platform != 'linux':
        raise SystemExit('Run only on test-env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', choices=('ordinary', 'ttl', 'wake', 'immediate', 'screen', 'import'))
    parser.add_argument('--batch', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        imp = args.scope == 'import'
        root, plan = inputs(args.batch, args.plan_sha256, IMPORT if imp else WAKE if args.scope == 'wake' else REOPEN,
                            'import_thumbnail' if imp else 'resident', 'off', 'idle' if imp else '45')
        result = import_qualification(root, plan) if imp else reopen(args.scope, root, plan)
    except Exception:
        # A failed replay can contain private paths in exception text.
        result = {'scope_name': args.scope, 'valid': False, 'qualified': False, 'issues': ['private_replay_failed']}
    result.update(frozen_plan_sha256=args.plan_sha256, r3_launcher_sha256=LAUNCH,
                  replay_sha256=sha(Path(__file__).read_bytes()))
    with args.output.open('x') as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write('\n')
    print(json.dumps({k: result[k] for k in ('scope_name', 'valid', 'qualified')}))
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    sys.exit(main())
