#!/usr/bin/env python3
"""Qualify frozen R3 launcher evidence on test-env, without device access."""

import argparse
from hashlib import file_digest, sha256
import json
from pathlib import Path
import re
import sys
import tarfile
from types import ModuleType

BASE = Path('/root/aidoku-all-bench-20261119')
BUILD = Path('/root/aidoku-all-build-20261119')
RELEASE = BUILD / 'artifacts/final-v3-clean'
RECEIPT = BUILD / 'reports/final-v3-clean/build-receipt.json'
RECEIPT_SHA = '516d572640a5f04b3243180b42b5f792f80687664febdac182f2bc8dc6e083c8'
SOURCE = '28c1bb314e12fe5689b401ca23cf932a91c138f5763c4c1ea18d454f41c990cf'
APP = '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
LAUNCH = '412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675'
HELPER = 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
BACKEND = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
CONTROL = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
CONTROL_LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
CAPTURE = 'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90'
MEASURE = '113991a8c5668fb050a0dbb368267a34868b0cf95c4992ba501fdb2c4ee1a323'
CREATOR = 'c931d66e11a0f35365d7c076b4af782b5f51524229cb01a56b48ac9e6654b055'
QUALIFIER = '5a7aa1f10fec8f31cb7e30509f1e070bbde25dbbcf570610b949858fa68214b2'
PORTRAIT_RUNNER = '28af8484760c609c8822de500b24fa7e5ae31faace91a31b653bb6f408e76f5c'
EXTRA = 'aidoku-startup-portrait'
FILES = {'candidate-arm': APP, 'launch.sh': LAUNCH, EXTRA: HELPER}
PACKAGE = {'aidoku/app/aidoku': APP, 'aidoku/launch.sh': LAUNCH, f'aidoku/app/{EXTRA}': HELPER}
CHOICES = {'layout': ('first_layout', 'baseline', 'optimized'),
           'prewarm': ('prewarm', '0', '2'), 'grace': ('last_cover_grace', 'off', 'on'),
           'supervisor': ('portrait', 'shell', 'native')}
SIDES = ('baseline', 'candidate')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def regular(path):
    require(path.is_file() and not path.is_symlink(), 'missing_or_linked_evidence_file')


def digest(path):
    regular(path)
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def exact(path, expected):
    regular(path)
    data = path.read_bytes()
    require(sha256(data).hexdigest() == expected, 'frozen_input_digest_mismatch')
    return data


def load(name, path, expected):
    data = exact(path, expected)
    mod = ModuleType('_startup_r3_' + name)
    mod.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), mod.__dict__)
    return mod


def read_record(path):
    def unique(pairs):
        value = dict(pairs)
        require(len(value) == len(pairs), 'duplicate_evidence_field')
        return value

    regular(path)
    require(path.stat().st_size <= 1024 * 1024, 'oversized_evidence_record')
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def verify_release():
    receipt = json.loads(exact(RECEIPT, RECEIPT_SHA))
    require(receipt['source_manifest_sha256'] == SOURCE and receipt['program_sha256'] == PACKAGE
            and receipt['packaging_sha256']['packaging/kindle/launch.sh'] == LAUNCH,
            'r3_producer_receipt_mismatch')
    for member, identity in FILES.items():
        path = RELEASE / member
        require(digest(path) == identity, 'r3_release_member_mismatch')
        if member != 'launch.sh':
            with path.open('rb') as stream:
                header = stream.read(52)
            require(len(header) == 52 and header.startswith(b'\x7fELF\x01\x01\x01')
                    and int.from_bytes(header[18:20], 'little') == 40, 'r3_member_not_arm_elf')


def base_measure():
    base = load('measure', BASE / 'all-options/frozen-analyze-all.py', MEASURE)
    base.DEFAULTS = {**base.DEFAULTS, 'import_thumbnail': 'off'}
    return base


def check_variants(spec, trial, full=None):
    for index, side in enumerate(SIDES):
        arm = spec[side]
        control = trial == 'combined' and side == 'baseline'
        app, launcher, extra = ((CONTROL, CONTROL_LAUNCH, {}) if control else (APP, LAUNCH, {EXTRA: HELPER}))
        options = {}
        if trial in CHOICES:
            key, *values = CHOICES[trial]
            options = {key: values[index]}
        elif trial == 'all-options' and side == 'candidate':
            options = full
        require((arm['application_sha256'], arm['launcher_sha256'], arm['extra_sha256'], arm['native_query_sha256'])
                == (app, launcher, extra, BACKEND) and arm['args'] == [] and arm['env'] == {}
                and arm['startup_options'] == options, 'r3_program_or_file_policy_mismatch')


def check_payload(root, plan, frozen):
    runner_hash, device_hash = frozen
    require((plan['runner_sha256'], plan['device_script_sha256']) == frozen
            and digest(root / 'device.sh') == device_hash, 'r3_frozen_harness_mismatch')
    runner = load('runner', root / 'runner.py', runner_hash)
    original = exact(root / 'original-launcher', CONTROL_LAUNCH)
    launcher = exact(RELEASE / 'launch.sh', LAUNCH)
    run, names, mode = plan['run_id'], plan['cases'], plan['mode']
    require(re.fullmatch(r'[a-z0-9-]{1,64}', run) and root.name == run, 'r3_frozen_run_id_mismatch')
    first = int(names[2].rsplit('-', 1)[1]) if mode != 'qualify' else 1
    require(names == runner.case_order(mode, first, (len(names) - 2) // 2), 'r3_frozen_case_order_mismatch')
    bridge = runner.bridge(run, names, dict.fromkeys(SIDES, ':'))
    owner = (run + '\n').encode('ascii')
    require(sha256(bridge).hexdigest() == plan['bridge_sha256'] and sha256(owner).hexdigest() == plan['owner_hash'],
            'r3_frozen_bridge_mismatch')
    expected = {'owner': sha256(owner).hexdigest(), 'bridge.sh': sha256(bridge).hexdigest()}
    if mode == 'qualify':
        expected['capture'] = CAPTURE
    for side in SIDES:
        arm = plan['variants'][side]
        is_control = arm['application_sha256'] == CONTROL
        staged = runner.instrument((original if is_control else launcher).decode(), [])
        expected[f'{side}/launch.sh'] = sha256(staged).hexdigest()
        expected[f'{side}/bench.options'] = arm['options_sha256']
        expected[f'{side}/bench.options.sha256'] = sha256((arm['options_sha256'] + '\n').encode()).hexdigest()
        app = arm['application_sha256']
        expected[f'{side}/binary-hash'] = sha256((app + '\n').encode()).hexdigest()
        ref = 'installed' if is_control else app
        expected[f'{side}/binary-ref'] = sha256((ref + '\n').encode()).hexdigest()
        if not is_control:
            expected[f'bin-{APP}'] = APP
            expected[f'{side}/app/{EXTRA}'] = HELPER
    payload = root / 'payload.tar.gz'
    require(digest(payload) == plan['payload_sha256'], 'r3_frozen_payload_digest_mismatch')
    observed = {}
    with tarfile.open(payload, 'r|gz') as archive:
        for member in archive:
            require(member.isfile() and member.name in set(expected) | {'inputs.sha256'}
                    and member.name not in observed, 'r3_unplanned_payload_member')
            with archive.extractfile(member) as stream:
                if member.name == 'inputs.sha256':
                    require(member.size < 4096, 'r3_unplanned_payload_manifest_size')
                    lines = stream.read().decode('ascii').splitlines()
                    manifest = dict(tuple(reversed(line.split('  ', 1))) for line in lines)
                    require(len(lines) == len(expected) and manifest == expected, 'r3_payload_manifest_mismatch')
                    observed[member.name] = True
                else:
                    observed[member.name] = file_digest(stream, 'sha256').hexdigest()
    require(observed == {**expected, 'inputs.sha256': True}, 'r3_staged_program_or_options_mismatch')


def batch_path(path):
    path = path.resolve(strict=True)
    return path.parent if path.name == 'plan.json' else path


def checked_plan(root, measure, trial, frozen, full=None):
    plan = read_record(root / 'plan.json')
    require(plan['trial'] == trial, 'r3_trial_mismatch')
    measure.variants(plan, require)
    check_variants(plan['variants'], trial, full)
    check_payload(root, plan, frozen)
    return plan


def checked_observation(root, plan):
    report = read_record(root / 'report.json')
    require(all(report.get(key) == plan[key] for key in ('variants', 'payload_sha256', 'runner_sha256',
            'device_script_sha256', 'run_id', 'mode', 'cases')), 'r3_report_not_bound_to_frozen_payload')
    exact(root / 'original-program-backup.stdout', CONTROL_LAUNCH)


def provenance(result):
    result.update(release_generation='R3', analyzer_sha256=digest(Path(__file__)),
                  producer_receipt_sha256=RECEIPT_SHA, compiled_source_manifest_sha256=SOURCE,
                  native_query_sha256=BACKEND, expected_candidate_application_sha256=APP,
                  frozen_program_members=FILES, frozen_payload_verified=True,
                  r1_measurement_algorithm_sha256=MEASURE)
    return result


def analyze(paths, qualification, trial, workload, phase, candidate_sha256=APP, crop=None):
    require(candidate_sha256 == APP, 'Only the frozen R3 application is admitted')
    require(paths, 'A primary batch is required')
    verify_release()
    measure = base_measure()
    if trial == 'supervisor':
        require(workload == 'warm', 'R3 portrait needs its qualified warm harness')
        measure.FROZEN['warm'] = (PORTRAIT_RUNNER, measure.FROZEN['warm'][1])
    paths = [batch_path(path) for path in paths]
    qualification = [batch_path(path) for path in qualification]
    for path in paths + qualification:
        checked_observation(path, checked_plan(path, measure, trial, measure.FROZEN[workload]))
    return provenance(measure.analyze(paths, qualification, trial, workload, phase, APP, crop))


def all_options_modules(release_plan):
    measure = base_measure()
    directory = BASE / 'all-options/tools'
    creator = load('creator', directory / 'materialize_all_options.py', CREATOR)
    creator.DEST, creator.RELEASE = BASE / 'all-options-v3', RELEASE
    inherited_expected = creator.expected

    def expected(path):
        require(inherited_expected(path) == FILES, 'R3 all-options requires an R3 defaults plan')
        checked_plan(batch_path(path), measure, 'combined', measure.FROZEN['warm'])
        return FILES

    creator.expected = expected
    qualifier = load('qualifier', directory / 'analyze_all_options.py', QUALIFIER)
    qualifier.DEPOT = creator.DEST

    def reference(name, paths, expected_hash):
        if (name, expected_hash) == ('creator', CREATOR):
            return creator
        if (name, expected_hash) == ('measure', MEASURE):
            return base_measure()
        raise ValueError('Unrecognized R3 qualification dependency')

    qualifier.load = reference
    versions = creator.generate()
    qualifier.RUNNER = creator.digest(versions['run_bench.py'])
    qualifier.DEVICE = creator.digest(versions['bench_device.sh'])
    return creator, qualifier


def prepare_all(release_plan):
    verify_release()
    creator, _ = all_options_modules(release_plan)
    creator.prepare(release_plan)


def analyze_all_options(path, release_plan, crop=None):
    verify_release()
    creator, qualifier = all_options_modules(release_plan)
    path = batch_path(path)
    checked_observation(path, checked_plan(path, base_measure(), 'all-options',
                                        (qualifier.RUNNER, qualifier.DEVICE), creator.FULL))
    result = qualifier.analyze(path, release_plan, crop)
    result['r1_qualification_algorithm_sha256'] = QUALIFIER
    return provenance(result)


def main():
    if sys.platform != 'linux' or sys.flags.optimize:
        raise SystemExit('Run exclusively on test-env without Python optimization')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    prepare = commands.add_parser('prepare-all-options')
    prepare.add_argument('--release-plan', type=Path, required=True)
    all_options = commands.add_parser('analyze-all-options')
    all_options.add_argument('--batch', type=Path, required=True)
    all_options.add_argument('--release-plan', type=Path, required=True)
    measure = commands.add_parser('analyze')
    measure.add_argument('--batch', type=Path, required=True, action='append')
    measure.add_argument('--qualification-batch', type=Path, action='append', default=[])
    measure.add_argument('--trial', choices=('combined', *CHOICES), required=True)
    measure.add_argument('--workload', choices=('warm', 'cold'), required=True)
    measure.add_argument('--phase', choices=('pilot', 'screen', 'confirm', 'qualify'), required=True)
    measure.add_argument('--candidate-sha256', default=APP)
    for command in (measure, all_options):
        command.add_argument('--status-bar-pixels', type=int)
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == 'prepare-all-options':
            return prepare_all(args.release_plan)
        if args.operation == 'analyze-all-options':
            result = analyze_all_options(args.batch, args.release_plan, args.status_bar_pixels)
        else:
            result = analyze(args.batch, args.qualification_batch, args.trial, args.workload, args.phase,
                             args.candidate_sha256, args.status_bar_pixels)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (OSError, ValueError, KeyError, TypeError, IndexError, tarfile.TarError) as err:
        parser.exit(2, f'R3 evidence rejected: {err}\n')
    keys = ('trial', 'valid', 'issues', 'input_failures', 'complete_pairs', 'all_options_functional_passed',
            'latency_gate_passed', 'default_guard_passed', 'qualification_passed')
    print(json.dumps({key: result[key] for key in keys if key in result}))
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    sys.exit(main())
