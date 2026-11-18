#!/usr/bin/env python3
"""Freeze only candidates that passed screening, independent confirmation and release qualification.

Provide --selection, --source-root and a fresh --output on test-env. Selection
contains schema=1, source_manifest_sha256, artifacts keyed by package member,
candidates keyed by supervisor/cover/cold/status with screen and confirm references, and
qualification (pixel analysis) and lifecycle references. Every input reference
is {uri: absolute file URI, sha256: digest}. A combined selection also needs a
combination object with primary, screen and confirm for the final composition.
Cold admission accepts only a standalone application change and independently
matches its empty-cache fixtures across timing and pixel runs in private storage.
The single status/decode build also binds a separate six-pair warm_guard and
the physical Wi-Fi/battery pixels before and after the default sleep/wake.
This tool has no device transport or registration-writing capability.
"""

import argparse
import collections
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import stat
import statistics
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


BASE = Path('/root/aidoku-startup-delivery-20261118/artifacts')
COLD_PRIVATE = Path('/root/aidoku-next-coldbench-20261118/private')
ZIP_HASH = '2a29b944c273431a0e92bdc127ca434d922ef2cda8b97e959220688ffeda92b9'
KPM_HASH = 'f1906a69cc9037bc420adb2e53c310e9d418c4f7274fb000c1b51d13dd74d0b7'
REGISTRATION = ('/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885'
                'f692955cacd86/aidoku-library-dispatch')
APP, LAUNCH, EXTRA = 'aidoku/app/aidoku', 'aidoku/launch.sh', 'aidoku/app/aidoku-startup-portrait'
QUERY, JPEG = 'aidoku/app/aidoku-native-query', 'aidoku/app/kindle-jpeg-worker'
MEMBERS = {'supervisor': {LAUNCH, EXTRA}, 'cover': {APP}, 'cold': {APP}, 'status': {APP}}
METRICS = {'supervisor': ('usable', 'covers'), 'cover': ('covers', 'usable'), 'cold': ('covers', 'usable'),
           'status': ('covers', 'usable')}
MECHANISMS = {'supervisor': 'startup_portrait_supervisor', 'cover': 'bounded_cached_covers',
              'cold': 'cold_local_cover_decode_validation', 'status': 'cold_decode_with_fresh_toolbar_status'}
COLD_POLICY = {'cache_regime': 'empty-cache-v1', 'fixture_policy': 'per-case-empty-cache-v1'}
STATUS_APP = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
STATUS_SOURCE = '1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768'
STATUS_ANALYZER = '55e126239715708b4c623f164178a33f25a8c631a6232a7f5f8ad50a9006e569'
RIGHT_PIXELS = '6958232ab783a32ceb822c7ba13d083ec2470bb4bc13e3af675e352e0c703a20'
BASE_ANALYZER = 'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'
COLD_ANALYZER = '2d9a5d376691af829f85a936d428d9f498ae1b63b3d5423229374f5a7e87a839'
STATUS_WARM_POLICY = {'cache_regime': 'warm-local-derivatives-v1', 'fixture_policy': 'six-local-covers-warm-v1'}
DEPLOYED = {APP: '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612',
            LAUNCH: '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296',
            QUERY: '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'}
LIFECYCLE = ('pixels', 'input', 'exit', 'sleep_wake', 'notification', 'catalog_identity',
             'restoration', 'production_defaults')


def check(condition, description):
    if not condition:
        raise ValueError(description)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    check(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value), 'Expected a lower-case SHA-256')
    return value


def checked_path(path):
    check(path.is_absolute() and path.is_file() and path.resolve(strict=True) == path,
          f'Expected an absolute, nonsymlink regular input: {path}')
    return path


def load_bound(ref):
    check(isinstance(ref, dict) and set(ref) == {'uri', 'sha256'}, 'Invalid bound input reference')
    uri = urlsplit(ref['uri'])
    check(uri.scheme == 'file' and not uri.netloc and not uri.query and not uri.fragment,
          'Only absolute local file URIs are allowed')
    content = checked_path(Path(unquote(uri.path))).read_bytes()
    check(sha(content) == digest(ref['sha256']), 'Evidence or artifact digest differs')
    return content


def unique_json(raw):
    def no_duplicates(items):
        obj = dict(items)
        check(len(obj) == len(items), 'Duplicate JSON property')
        return obj
    return json.loads(raw, object_pairs_hook=no_duplicates)


def allowed_changes(candidates, artifacts):
    check(bool(candidates) and set(candidates) <= set(MEMBERS), 'Empty or unsupported selection')
    check(not ({'cold', 'status'} & set(candidates)) or len(candidates) == 1, 'Combined cold releases are not qualified')
    permitted = set.union(*(MEMBERS[name] for name in candidates))
    check(set(artifacts) == permitted, 'Selected changes do not match the production allowlist')
    return permitted


def bind_workload(report, candidate):
    check(report.get('mechanism', {}).get('name') == MECHANISMS[candidate], 'Analysis mechanism does not match candidate')
    records = [report, *report['batches']]
    if candidate == 'status':
        status_workload(report, 'cold')
    elif candidate == 'cold':
        check(all(all(r.get(k) == v for k, v in COLD_POLICY.items()) for r in records), 'Cold workload drifted')
        check(report.get('cold_fixture_verified') is True and report.get('cold_fixture_match') is True,
              'Missing cold fixture proof')
    else:
        check(all(all(k not in r for k in COLD_POLICY) for r in records), 'Warm trial contains a different cache workload')


def assert_same_seed(private_root, run_cases):
    # Never export a fingerprint of a private library or profile. Both files are read
    # locally on test-env, outside a lease, and only the equality result is retained.
    check(bool(run_cases), 'No cold runs available')
    first = None
    for run, cases in run_cases.items():
        check(re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', run) and cases, 'Invalid cold run')
        for case in sorted(cases):
            check(re.fullmatch(r'(?:baseline|candidate)-(?:[0-9]{2}|warmup|qualify)', case), 'Invalid cold case')
            directory = private_root / run / 'outputs/out' / case
            check(checked_path(directory / 'cold-fixture').read_bytes() == b'empty-cache-v1\n', 'Missing cold case marker')
            identity = checked_path(directory / 'cold-fixture.sha256').read_bytes()
            seed = checked_path(directory / 'seed.sha256').read_bytes()
            check(re.fullmatch(rb'[a-f0-9]{64}  library\.sqlite3\n[a-f0-9]{64}  device-profile\n', seed)
                  and identity == (sha(seed) + '\n').encode('ascii'), 'Cold seed proof differs')
            if first is None:
                first = identity
            check(first == identity, 'Cold runs do not share the same fixture')


def observed_artifacts(variant):
    digest(variant['variant_sha256'])
    extras = variant['extra_sha256']
    check(isinstance(extras, dict) and set(extras) <= {PurePosixPath(EXTRA).name}, 'Unexpected helper identity')
    return {APP: digest(variant['application_sha256']), LAUNCH: digest(variant['launcher_sha256']),
            QUERY: digest(variant['native_query_sha256']),
            **{f'aidoku/app/{name}': digest(value) for name, value in extras.items()}}


def bind_measurement(report, expected):
    variants = report['artifacts']
    actual = observed_artifacts(variants['candidate'])
    check(all(actual.get(name) == value for name, value in expected.items()), 'Unmeasured candidate bytes')
    check(observed_artifacts(variants['baseline'])[QUERY] == actual[QUERY], 'Native-query control differs')
    return actual


def release_trial_context(report, expected, combined=False):
    qualified = DEPLOYED | expected
    actual = observed_artifacts(report['artifacts']['candidate'])
    check(actual == qualified, 'Trial includes nonselected candidate program changes')
    control = observed_artifacts(report['artifacts']['baseline'])
    if combined:
        check(control == DEPLOYED, 'Combined comparison requires the deployed control')
    else:
        # A matching binary with an explicit off switch is a valid isolated
        # control. Every other program component must still be the deployed one.
        check(all(control.get(path) in {DEPLOYED.get(path), qualified.get(path)}
                  for path in {*DEPLOYED, *qualified, *control}), 'Trial uses an unrelated control artifact')
    check(report['artifacts']['candidate']['variant_sha256'] != report['artifacts']['baseline']['variant_sha256'],
          'Control and candidate program/configuration identities are identical')


def observation(row):
    for name in ('first', 'input_ready', 'usable', 'covers', 'ready', 'exit'):
        x = row[name]
        check(type(x) in (int, float) and math.isfinite(x) and x >= 0, f'Invalid timing: {name}')
    check(abs(row['usable'] - max(row['first'], row['input_ready'])) <= 0.002, 'Incorrect usable-frame endpoint')
    check(row['covers'] >= row['first'], 'Complete covers precede the first frame')
    return row


def status_workload(report, workload):
    cold = workload == 'cold'
    check(workload in ('cold', 'warm') and report.get('expected_candidate_application_sha256') == STATUS_APP
          and report.get('analyzer_sha256') == STATUS_ANALYZER and report.get('right_status_bar_analyzer_sha256') == RIGHT_PIXELS
          and report.get('warm_analyzer_sha256') == BASE_ANALYZER, 'Unqualified status/decode analysis or implementation')
    if cold:
        check(report.get('cold_analyzer_sha256') == COLD_ANALYZER and report.get('cold_fixture_verified') is True
              and report.get('cold_fixture_match') is True, 'Cold reference analyzer or fixture is unqualified')
    check(report['primary_endpoint'] == 'covers', 'Wrong endpoint for status/decode comparison')
    release_trial_context(report, {APP: STATUS_APP}, combined=True)
    for role, app in (('baseline', DEPLOYED[APP]), ('candidate', STATUS_APP)):
        v = report['artifacts'][role]
        config = {'application_sha256': app, 'launcher_sha256': DEPLOYED[LAUNCH], 'native_query_sha256': DEPLOYED[QUERY],
                  'extra_sha256': {}, 'env': {}, 'args': []}
        check(v['variant_sha256'] == sha(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()),
              'Comparison overrides production defaults')
    policy = COLD_POLICY if cold else STATUS_WARM_POLICY
    for row in [report, *report['batches']]:
        st, dec, both = [row.get(k, {}) for k in ('status_mechanism', 'cold_mechanism', 'mechanism')]
        check(row.get('workload') == workload and all(row.get(k) == v for k,v in policy.items()) and
              row['artifacts'] == report['artifacts'], 'Mixed artifact or cache regime')
        check(both.get('name') == MECHANISMS['status'] and both.get('workload') == workload and both.get('verified') is True
              and st.get('name') == 'consume_fresh_toolbar_status_in_first_library_view' and st.get('verified') is True
              and both.get('toolbar_verified') is True and dec.get('name') == MECHANISMS['cold']
              and dec.get('verified') is cold and both.get('cold_decode_verified') is cold,
              'Missing measured toolbar or cold-decode mechanism')
        if not cold:
            check(dec.get('applicable') is False, 'Warm trials cannot prove cold misses')


def qualify_status(report):
    status_workload(report, 'cold')
    check(report['valid'] is True and report.get('qualification_passed') is True, 'Unverified status/decode pixels')
    ids = {b['run_id'] for b in report['batches'] if b['mode'] == 'qualify'}
    pixels = report['pixels']; region = {'x': 930, 'y': 0, 'width': 930, 'height': 72}
    check(ids and len(ids) == len(pixels) and ids == {r['batch'] for r in pixels}, 'Missing distinct toolbar pixel batch')
    check(all((r.get('width'),r.get('height'),r.get('status_bar_excluded_pixels')) == (1860,2480,72)
              and r.get('right_status_bar_region') == region and r.get('right_status_bar_observable') is True
              and r.get('identical_right_status_bar') is True and r.get('identical_below_status_bar') is True for r in pixels),
          'Right toolbar or Library pixels did not qualify')
    return ids


def freeze_gate(report, phase, candidate, expected):
    check(phase in ('screen', 'confirm') and candidate in METRICS, 'Unknown phase or candidate')
    check(candidate == 'status' or (expected.get(APP) != STATUS_APP and
          report.get('artifacts', {}).get('candidate', {}).get('application_sha256') != STATUS_APP),
          'The frozen status/decode build cannot be relabeled as an earlier optimization')
    check(report['phase'] == phase and report['primary_endpoint'] == ('first' if candidate == 'supervisor' else 'covers'),
          'Different stage or endpoint')
    check(report['valid'] is True and report['latency_gate_passed'] is True and
          report.get('mechanism', {}).get('verified') is True, 'Latency or mechanism gate did not pass')
    bind_workload(report, candidate)
    bind_measurement(report, expected)
    if candidate in ('cold', 'status'):
        check(set(expected) == {APP} and expected[APP] != DEPLOYED[APP], 'No separate cold candidate executable')
        release_trial_context(report, expected, combined=True)
    n, wins = (6, 5) if phase == 'screen' else (10, 9)
    batches, savings = paired_savings(report, phase, n)
    primary, secondary = METRICS[candidate]
    check(sum(x > 0 for x in savings[primary]) >= wins and statistics.median(savings[primary]) >= 100,
          'Primary endpoint misses the practical benefit or consistency threshold')
    check(statistics.median(savings[secondary]) >= -50, 'Secondary endpoint regresses too far')
    return batches


def paired_savings(report, phase, n):
    batches, nested = set(), []
    for batch in report['batches']:
        if not batch.get('pairs') and batch['mode'] in ('qualify', 'pilot'):
            continue
        rid = batch['run_id']
        check(isinstance(rid, str) and rid and rid not in batches and batch['mode'] == phase, 'Duplicate or wrong batch')
        check(batch['artifacts'] == report['artifacts'], 'Phase contains different artifact identities')
        check(batch['pairs'] and all(p['batch'] == rid for p in batch['pairs']), 'Pair missing batch identity')
        batches.add(rid)
        nested.extend(batch['pairs'])
    rows = report['pairs']
    check(len(rows) == n == len(nested) and [r['round'] for r in rows] == list(range(1, n + 1)),
          'Required number of ordered, distinct pairs not present')
    check(rows == nested, 'Batch and phase observations disagree')
    check(collections.Counter(r['order'] for r in rows) == {'AB': n // 2, 'BA': n // 2}, 'Unbalanced pair order')
    savings = {'usable': [], 'covers': []}
    for row in rows:
        check(row['complete'] is True and row['batch'] in batches, 'Incomplete pair or unexpected batch')
        baseline, variant = observation(row['baseline_ms']), observation(row['candidate_ms'])
        for key in savings:
            savings[key].append(round(baseline[key] - variant[key], 3))
    return batches, savings


def warm_status_guard(report, expected):
    check(expected == {APP: STATUS_APP} and report['phase'] == 'screen' and report['valid'] is True
          and report.get('warm_guard_passed') is True, 'Missing status warm guard')
    status_workload(report, 'warm')
    batches, savings = paired_savings(report, 'screen', 6)
    check(all(statistics.median(x) >= -50 for x in savings.values()), 'Observed warm endpoint regression')
    return batches


def qualify_measured(measurement, lifecycle, expected):
    check(measurement['valid'] is True and measurement['qualification_passed'] is True, 'Missing pixel qualification')
    bind_measurement(measurement, expected)
    ids = {b['run_id'] for b in measurement['batches']
           if b['mode'] == 'qualify' and b['artifacts'] == measurement['artifacts']}
    evidence = measurement['pixels']
    check(evidence and ids and {e['batch'] for e in evidence} == ids, 'Missing independent pixel batch')
    check(all(e['identical_below_status_bar'] is True and e['width'] > 0 and
              0 <= e['status_bar_excluded_pixels'] < e['height'] for e in evidence), 'Pixel qualification failed')
    check(lifecycle['artifact_sha256'] == expected, 'Lifecycle qualification has different artifact identities')
    check(all(lifecycle['checks'].get(k) is True for k in LIFECYCLE), 'Incomplete lifecycle qualification')
    return ids


def kpm_payload_manifest(archive):
    return ''.join(
        f'{sha(blob)}|{len(blob)}|{mode:o}|{name[8:]}\n'
        for name, (blob, mode) in sorted(archive.items()) if name.startswith('payload/')
    ).encode('ascii')


def check_archive_delta(baseline, current, changes, kpm=False):
    prefix = 'payload/' if kpm else ''
    check(bool(changes) and set(changes) <= {APP, LAUNCH, EXTRA}, 'Non-program change')
    expected_names = set(baseline)
    changed_names = set()
    for path, (old, new) in changes.items():
        name = prefix + path
        if old == 'absent':
            check(path == EXTRA and name not in baseline, 'Only a missing supervisor can be added')
            expected_names.add(name)
        else:
            check(name in baseline and sha(baseline[name][0]) == digest(old), 'Incorrect original member')
        check(name in current and sha(current[name][0]) == digest(new) and current[name][1] == 0o755,
              'New member has different content or permissions')
        check(old != new, 'Unchanged file in delivery delta')
        changed_names.add(name)
    check(set(current) == expected_names, 'Unexpected, missing, or renamed archive member')
    if kpm:
        for archive in (baseline, current):
            check(archive['payload.tsv'] == (kpm_payload_manifest(archive), 0o644), 'Incorrect KPM payload manifest')
        changed_names.add('payload.tsv')
    check(all(current[name] == baseline[name] for name in baseline if name not in changed_names),
          'Protected archive member changed')


def safe_member(name):
    check(isinstance(name, str) and re.fullmatch('[A-Za-z0-9_./-]+', name)
          and PurePosixPath(name).as_posix() == name and not name.startswith('/')
          and not (set(name.split('/')) & {'', '.', '..', 'data'}), 'Unexpected archive path')


def read_archive(path, kind):
    files = {}
    if kind == 'zip':
        with zipfile.ZipFile(path) as opened:
            for entry in opened.infolist():
                safe_member(entry.filename)
                raw_mode = entry.external_attr >> 16
                check(entry.create_system == 3 and stat.S_ISREG(raw_mode) and entry.filename not in files,
                      'Unexpected or duplicate ZIP member')
                files[entry.filename] = (opened.read(entry), stat.S_IMODE(raw_mode))
        return files
    dirs, names = set(), set()
    with tarfile.open(path, 'r:gz') as opened:
        for entry in opened:
            safe_member(entry.name)
            check(entry.name not in names and entry.uid == 0 and entry.gid == 0,
                  'Duplicate KPM member or changed ownership')
            names.add(entry.name)
            if entry.isdir():
                check(entry.mode == 0o755, 'Unexpected KPM directory permissions')
                dirs.add(entry.name)
            else:
                check(entry.isreg() and entry.mode in (0o644, 0o755), 'Invalid KPM payload type or mode')
                files[entry.name] = (opened.extractfile(entry).read(), entry.mode)
    expected_dirs = {str(directory) for name in files for directory in PurePosixPath(name).parents if str(directory) != '.'}
    check(dirs == expected_dirs, 'Unexpected KPM directory layout')
    return files


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def require_lifecycle_replay(lifecycle, source, qualification_hash):
    run = lifecycle.get('run_id')
    check(isinstance(run, str) and re.fullmatch(r'aidoku-next-life-[0-9a-f]{8}', run), 'No independently recorded lifecycle run')
    pinned = digest(lifecycle.get('qualification_plan_sha256'))
    path = checked_path(Path(__file__).resolve().with_name('analyze_lifecycle.py'))
    check(lifecycle.get('analyzer_sha256') == sha(path.read_bytes()), 'Lifecycle result came from a different analyzer')
    spec = importlib.util.spec_from_file_location('release_lifecycle_replay', path)
    analyzer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analyzer)
    batch = analyzer.HOST / run
    original_pixels = checked_path(batch / 'pixels.json')
    check(sha(original_pixels.read_bytes()) == qualification_hash, 'Lifecycle was not bound to the selected visual evidence')
    check(analyzer.analyze(batch, original_pixels, source, pinned) == lifecycle,
          'Lifecycle qualification cannot be reproduced from the independent raw observations')


def match_release_references(selection, lifecycle):
    references = {'pixels': selection['qualification']['sha256']}
    entries = selection['candidates']
    for name, cfg in entries.items():
        prefix = name + '_' if len(entries) > 1 else ''
        for key in ('screen', 'confirm'):
            references[prefix + key] = cfg[key]['sha256']
        if name == 'status':
            references['warm_guard'] = cfg['warm_guard']['sha256']
    if len(entries) > 1:
        references.update({key: selection['combination'][key]['sha256'] for key in ('screen','confirm')})
    check(lifecycle.get('reference_hashes') == references, 'Release inputs differ from the evidence replayed for default qualification')


def build_staged(selection, sources, path, raw_selection_hash):
    references = selection['artifacts']
    allowed = allowed_changes(selection['candidates'], references)
    check(selection['schema'] == 1, 'Invalid selection schema')
    evidence = {}

    def bound_report(label, ref):
        obj = unique_json(load_bound(ref))
        evidence[label] = ref['sha256']
        return obj

    blobs = {name: load_bound(references[name]) for name in allowed}
    claimed = {name: sha(data) for name, data in blobs.items()}
    # This application passed cold confirmation but failed the six-pair warm
    # usability guard (median -67.421 ms). Preserve its historical analyses.
    check(claimed.get(APP) != 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6',
          'The standalone cold-decode application failed the warm guard and cannot ship')
    seen = set()
    cold_cases = {}
    cold_variants = None

    def remember_cold_cases(analysis, runs):
        for batch in analysis['batches']:
            if batch['run_id'] not in runs:
                continue
            names = {r['case'] for r in batch['cases'] if r['status'] == 'ok'}
            check(names and len(names) == len(batch['cases']), 'Incomplete cold case records')
            if batch['mode'] == 'qualify':
                required = {'baseline-qualify', 'candidate-qualify'}
            else:
                required = {f"{side}-{p['round']:02}" for side in ('baseline', 'candidate') for p in batch['pairs']}
            check(required <= names, 'Cold case records do not cover the planned endpoints')
            cold_cases[batch['run_id']] = names

    def accept(name, definition, expected, label):
        nonlocal cold_variants
        stages = {stage: bound_report(label + '_' + stage, definition[stage]) for stage in ('screen', 'confirm')}
        for stage, report in stages.items():
            groups = freeze_gate(report, stage, name, expected)
            release_trial_context(report, expected, combined=label == 'combination')
            if name in ('cold', 'status'):
                remember_cold_cases(report, groups)
                cold_variants = report['artifacts']
            check(groups.isdisjoint(seen), 'Screening/confirmation reused timing batches')
            seen.update(groups)
        check(stages['screen']['artifacts'] == stages['confirm']['artifacts'],
              'Screening and confirmation use different measured inputs')
        if name == 'status':
            warm = bound_report('warm_guard', definition['warm_guard'])
            warm_ids = warm_status_guard(warm, expected)
            check(warm_ids.isdisjoint(seen) and warm['artifacts'] == cold_variants,
                  'Warm guard must independently exercise the exact release composition')
            seen.update(warm_ids)

    for name, definition in selection['candidates'].items():
        accept(name, definition, {k: claimed[k] for k in MEMBERS[name]}, name)
    if len(selection['candidates']) > 1:
        combo = selection['combination']
        accept(combo['primary'], combo, claimed, 'combination')
    else:
        check('combination' not in selection, 'Unnecessary combined acceptance record')

    qualification = bound_report('pixels', selection['qualification'])
    lifecycle = bound_report('lifecycle', selection['lifecycle'])
    visual_runs = qualify_measured(qualification, lifecycle, claimed)
    match_release_references(selection, lifecycle)
    require_lifecycle_replay(lifecycle, sources, evidence['pixels'])
    check(seen.isdisjoint(visual_runs), 'Pixel runs overlap primary timings')
    workload = (selection['combination']['primary'] if 'combination' in selection else next(iter(selection['candidates'])))
    bind_workload(qualification, workload)
    if workload == 'status':
        check(qualify_status(qualification) == visual_runs and lifecycle['checks'].get('right_status_bar') is True,
              'No independently matched toolbar pixels on both production defaults and wake')
    if workload in ('cold', 'status'):
        check(qualification['mechanism'].get('verified') is True and qualification['artifacts'] == cold_variants,
              'Cold pixel run must exercise the same cold comparison')
        check(all(lifecycle.get(k) == v for k, v in COLD_POLICY.items())
              and lifecycle.get('mechanism') == MECHANISMS[workload], 'Lifecycle qualification used a different cold workload')
        remember_cold_cases(qualification, visual_runs)
        assert_same_seed(COLD_PRIVATE, cold_cases)
    actual = observed_artifacts(qualification['artifacts']['candidate'])
    check(actual == (DEPLOYED | claimed), 'Final qualification differs from the actual delivery composition')
    source_sha = digest(selection['source_manifest_sha256'])
    if workload == 'status':
        check(source_sha == STATUS_SOURCE, 'Source fingerprint differs from the frozen status/decode build')
    check(lifecycle.get('source_manifest_sha256') == source_sha, 'Unqualified final source')

    old_zip, old_kpm = [BASE / ('aidoku-startup-20261118.' + ext) for ext in ('zip', 'kpkg')]
    check(sha(checked_path(old_zip).read_bytes()) == ZIP_HASH and sha(checked_path(old_kpm).read_bytes()) == KPM_HASH,
          'The original installation archive no longer matches')
    base_zip, base_kpm = read_archive(old_zip, 'zip'), read_archive(old_kpm, 'kpm')
    changes = {name: (sha(base_zip[name][0]) if name in base_zip else 'absent', claimed[name]) for name in allowed}
    check(all(old != new for old, new in changes.values()), 'A delivery member is unchanged')
    check(all(sha(base_zip[name][0]) == fixed for name, fixed in DEPLOYED.items()), 'Baseline program identity differs')
    check(sha(checked_path(sources / 'packaging/kindle/launch.sh').read_bytes()) ==
          claimed.get(LAUNCH, DEPLOYED[LAUNCH]), 'Release tree does not have the selected launcher')

    scripts = sources / 'scripts'
    verifier_path = checked_path(scripts / 'verify-kindle-package.py')
    spec = importlib.util.spec_from_file_location('aidoku_package_verifier', verifier_path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    manifest = verifier.source_manifest(sources)
    check(source_sha == manifest['sha256'], 'The supplied source tree differs from qualification')
    write_json(path / 'source-manifest.json', dict(manifest, algorithm=verifier.SOURCE_ALGORITHM))
    with tempfile.TemporaryDirectory(prefix='staging-', dir=path) as temp_name:
        staging = Path(temp_name)
        for member in sorted(set(base_zip) | allowed):
            destination = staging / member
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(blobs[member] if member in blobs else base_zip[member][0])
        zip_path, kpm_path = path / 'aidoku-startup-next.zip', path / 'aidoku-startup-next.kpkg'

        def command(arguments):
            subprocess.run([sys.executable, '-I', *map(str, arguments)], cwd=sources, check=True)

        command([checked_path(scripts / 'package-kindle.py'), '--staging', staging, '--output', zip_path,
                 '--source-manifest', path / 'source-manifest.json'])
        command([checked_path(scripts / 'package-kpm.py'), '--application', staging / APP,
                 '--source-root', sources, '--output', kpm_path, '--native-query-helper', staging / QUERY,
                 '--native-catalog-dir', staging / 'aidoku/native-catalog',
                 *(['--startup-portrait-helper', staging / EXTRA] if EXTRA in allowed else [])])
        command([verifier_path, '--archive', zip_path, '--manifest', zip_path.with_suffix('.manifest.json'),
                 '--source-root', sources, '--source-manifest', path / 'source-manifest.json',
                 '--expected-source-sha256', source_sha, '--expected-application-sha256', actual[APP],
                 '--expected-helper-sha256', sha(base_zip[JPEG][0]), '--expected-native-query-sha256', actual[QUERY],
                 '--expected-catalog-dir', staging / 'aidoku/native-catalog', '--output', path / 'package-verification.json',
                 *(['--expected-startup-portrait-sha256', claimed[EXTRA]] if EXTRA in allowed else [])])
        for file, old, kind in ((zip_path, base_zip, 'zip'), (kpm_path, base_kpm, 'kpm')):
            check_archive_delta(old, read_archive(file, kind), changes, kpm=kind == 'kpm')
    for name, data in blobs.items():
        f = path / 'delta' / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        f.chmod(0o755)
    release_order = (EXTRA, APP, LAUNCH)
    return {'schema': 1, 'archive_sha256': sha(zip_path.read_bytes()), 'kpm_sha256': sha(kpm_path.read_bytes()),
            'catalog_registry': REGISTRATION, 'source_manifest_sha256': source_sha, 'selection_sha256': raw_selection_hash,
            'evidence_sha256': evidence, 'candidates': sorted(selection['candidates']),
            **({'cold_fixture_verified': True, **COLD_POLICY} if workload in ('cold', 'status') else {}),
            'changes': [{'path': name, 'old': changes[name][0], 'new': claimed[name], 'file': f'delta/{name}'}
                        for name in release_order if name in allowed]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check(platform.system() == 'Linux', 'Run only through ssh test-env')
    check(args.source_root.is_absolute() and args.source_root.resolve(strict=True) == args.source_root,
          'Expected a canonical source root')
    output = args.output
    check(output.is_absolute() and output.parent.resolve(strict=True) == output.parent and not output.exists(),
          'Expected a fresh, canonical output destination')
    raw = checked_path(args.selection).read_bytes()
    with tempfile.TemporaryDirectory(prefix='aidoku-promotion-', dir=output.parent) as temp_name:
        scratch = Path(temp_name) / 'release'
        scratch.mkdir(mode=0o700)
        result = build_staged(unique_json(raw), args.source_root, scratch, sha(raw))
        write_json(scratch / 'plan.json', result)
        # Reserve the destination so concurrent invocations cannot replace another plan.
        output.mkdir(mode=0o700)
        os.rename(scratch, output)
    print(json.dumps({'plan': str(output / 'plan.json'), 'changes': len(result['changes'])}))


if __name__ == '__main__':
    main()
