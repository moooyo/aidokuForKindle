#!/usr/bin/env python3
"""Reconstruct isolated production-default lifecycle qualification from private observations on test-env.

No device connection is made. Neither the decoded inputs nor their private hashes,
catalog rows, process identities, screen captures or transport records are exported.
The physical run tests verified input ownership and cooperative TERM, not a physical tap.
"""

import argparse
import importlib.util
import json
import re
import sys
import tarfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path


HERE = Path(__file__).resolve().parent
HOST = Path('/root/aidoku-next-lifecycle-20261118/private')
WARM = Path('/root/aidoku-next-bench-20261118')
COLD = Path('/root/aidoku-next-coldbench-20261118')
CASES = ['baseline-default', 'candidate-default']
SIDES = ['baseline', 'candidate']
OBS = 'request ready library request_sleep parked request_wake wake restored_frame request_term ordinary_exit native_restored'.split()
FRAME = b'width=1860 height=2480 bpp=8 p6_header_bytes=36 p6_bytes=13838436 crop_top_px=72\n'
JPEG_SHA = '1b41abcab8083ef70d3cb76ad16303d8b7934c382c4e49927f9789d11a7abf41'
PROFILE = b'scribe'
HEX = rb'[a-f0-9]{64}'
IDENTITY = (rb'[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}\|text/x-shellscript\|'
            rb'[^|\r\n]+\|[0-9]{1,14}\|[0-9]{1,7}\n')


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


gate = load('_prepare_lifecycle', HERE / 'prepare.py')
maker = load('_make_default_lifecycle', HERE / 'default_lifecycle.py')
check, sha = gate.check, gate.sha


def raw(path, limit=1024 * 1024):
    check(path.is_absolute() and path.is_file() and path.resolve(strict=True) == path and path.stat().st_size <= limit,
          'Required private evidence is missing, unbounded, or indirect')
    return path.read_bytes()


def line(path, pattern, limit=256):
    data = raw(path, limit)
    check(re.fullmatch(pattern + rb'\n', data), 'Malformed observation')
    return data[:-1]


def pairs(path, sep=b'\t'):
    rows = [r.split(sep) for r in raw(path, 64 * 1024).splitlines()]
    check(all(len(r) == 2 for r in rows) and len({r[0] for r in rows}) == len(rows), 'Invalid or duplicate observation')
    return {k.decode('ascii'): v.decode('ascii') for k, v in rows}


def observed_toolbar(path):
    # The device hashes the lowercase hex stream of RGB bytes while collecting
    # extrema from the same crop. A uniform crop does not demonstrate any icons.
    byte = rb'(?:0|[1-9][0-9]{0,2})'
    fields = line(path, rb'930\t0\t930\t72\t200880\t(?:' + byte + rb'\t){6}' + HEX).split(b'\t')
    limits = tuple(int(v) for v in fields[5:11])
    ranges = tuple(zip(limits[::2], limits[1::2]))
    check(all(0 <= lo <= hi <= 255 for lo, hi in ranges) and any(lo < hi for lo, hi in ranges),
          'Default right status bar is invalid or blank')
    return (*limits, fields[11])


def case_evidence(directory, run, side, expected_program, cache_regime, check_toolbar=False):
    check(side in SIDES and directory.name == side + '-default' and re.fullmatch(r'aidoku-next-life-[0-9a-f]{8}', run),
          'Wrong production-default case')
    check(raw(directory / 'case-ok') == b'ok\n', 'Device case did not complete')
    stage = ('/mnt/us/.' + run + '/' + side).encode('ascii')
    args = {'app': [stage + b'/app/aidoku', b'--kindle', b'--device', PROFILE, b'--data-dir', stage + b'/data'],
            'worker': [b'sh', stage + b'/launch.sh', b'--foreground']}
    defaults = []
    for role, words in args.items():
        defaults.append(raw(directory / (role + '.argv.nul')) == b'\x00'.join(words) + b'\x00' and
                        raw(directory / (role + '.env-relevant')) == b'AIDOKU_HOME=' + stage + b'\n')
    check(all(defaults), 'Non-production invocation or an experimental environment switch')
    installed = {'aidoku/' + k: v for k, v in pairs(directory / 'installed-hashes.tsv').items()}
    check(installed == expected_program and line(directory / 'app-exe.sha', HEX).decode() == installed[gate.APP],
          'Staged or actually executing program differs')

    identities = [line(directory / ('proc-' + phase + '.txt'), rb'[1-9][0-9]*(?: [1-9][0-9]*){5}')
                  for phase in ('active', 'parked', 'wake')]
    check(len(set(identities)) == 1, 'Process, runner or worker was replaced across the observed cycle')
    app, app_birth, runner, runner_birth, worker, worker_birth = [int(x) for x in identities[0].split()]
    check(len({app, runner, worker}) == 3 and min(app, runner, worker) > 1 and worker_birth <= runner_birth <= app_birth,
          'Invalid process ancestry or birth stamps')
    def fds(phase):
        values = raw(directory / ('input-' + phase + '.txt')).splitlines()
        check(values == sorted(set(values)) and all(re.fullmatch(rb'event[0-9]+', v) for v in values),
              'Invalid input descriptor observation')
        return set(values)
    active, _, resumed = [fds(i) for i in ('active', 'parked', 'wake')]
    check(active & resumed, 'No common input device before and after wake')
    states = []
    counters = ('epoch', 'pause_count', 'resume_count', 'same_process_resumes', 'frame_send_count',
                'resume_full_frame_count', 'input_generation')
    for phase in ('parked', 'wake'):
        st = pairs(directory / ('lifecycle-' + phase + '.tsv'))
        parked = phase == 'parked'
        check(st.get('pid') == str(app) and st.get('phase') == ('suspended' if parked else 'active') and
              all(st.get(k) == ('true' if parked else 'false') for k in ('input_released', 'render_paused', 'parked')),
              'Display or input was not released and reacquired by the observed process')
        check(all(re.fullmatch('[0-9]+', st.get(k, '')) for k in counters), 'Invalid lifecycle counters')
        states.append({k: int(st[k]) for k in counters})
    before, after = states
    epoch = after['epoch']
    check(before['epoch'] == epoch > 0 and before['pause_count'] >= 1 and after['pause_count'] == before['pause_count']
          and after['resume_count'] > before['resume_count'] and after['same_process_resumes'] == before['same_process_resumes'] + 1
          and after['frame_send_count'] > before['frame_send_count'] and after['resume_full_frame_count'] > before['resume_full_frame_count']
          and after['input_generation'] >= before['input_generation'], 'Wake is not a completed same-process full refresh')
    ack = f'{app}\t{epoch}\n'.encode('ascii')
    check(all(raw(directory / (name + '.tsv')) == ack for name in ('sleep-ready', 'resume-ready', 'ordinary-exit')),
          'Missing bound sleep, resume or cooperative exit acknowledgement')

    marks = [r.split(b'\t') for r in raw(directory / 'evidence.tsv').splitlines()]
    check(len(marks) == len(OBS) and all(len(r) == 2 and re.fullmatch(rb'[0-9]+\.[0-9]{1,6}', r[0]) for r in marks)
          and [r[1].decode('ascii') for r in marks] == OBS, 'Missing or duplicate lifecycle event')
    stamps = [Decimal(r[0].decode('ascii')) for r in marks]
    check(stamps == sorted(stamps) and stamps[0] < stamps[-1], 'Lifecycle event order differs')
    lines = raw(directory / 'launcher.log').splitlines()
    prefixes = (b'aidoku: Launcher worker started (pid=', b'aidoku: native window ready;',
                b'aidoku: Catalog notification completed.', b'aidoku: Launcher worker exited with status')
    matched = [x for x in lines if x.startswith(prefixes)]
    check(len(matched) == 4 and matched[0] == f'aidoku: Launcher worker started (pid={worker}).'.encode()
          and matched[1].startswith(b'aidoku: native window ready; profile=scribe, logical=930x1240, scale=2')
          and matched[2] == prefixes[2] and matched[3] == b'aidoku: Launcher worker exited with status 0.'
          and not any(b'catalog notification unavailable' in x.lower() or b'standard reindex recovery' in x.lower() for x in lines),
          'Incorrect worker lifetime, catalog notification, recovery or exit')
    if gate.EXTRA in expected_program:
        check([x for x in lines if x.startswith(b'aidoku: Startup portrait supervisor: ')] ==
              [b'aidoku: Startup portrait supervisor: native.'], 'Default supervisor was not exercised')

    catalog = raw(directory / 'catalog-before.identity', 32768)
    check(bool(re.fullmatch(IDENTITY, catalog)) and raw(directory / 'catalog-after.identity', 32768) == catalog,
          'Catalog entry, thumbnail or metadata changed')
    access_before, access_after = [int(line(directory / ('catalog-' + s + '.access'), rb'[0-9]{1,14}')) for s in ('before', 'after')]
    check(access_before < access_after, 'Recent access did not advance')
    check(raw(directory / 'frame-format.tsv') == FRAME, 'Invalid physical geometry, crop or capture format')
    frame, wake = [line(directory / path, HEX) for path in ('library.sha', 'wake.sha')]
    check(frame == wake, 'Library pixels did not survive wake')
    toolbars = {}
    if check_toolbar:
        toolbars['toolbar'] = observed_toolbar(directory / 'library.right.tsv')
        toolbars['wake_toolbar'] = observed_toolbar(directory / 'wake.right.tsv')
        check(toolbars['toolbar'] == toolbars['wake_toolbar'], 'Default right status bar differs after wake')
    check(raw(directory / 'fixture-regime.txt') == (cache_regime + '\n').encode('ascii'), 'Wrong fixture regime')
    seed = raw(directory / 'seed.sha256', 256)
    check(re.fullmatch(rb'[a-f0-9]{64}  library\.sqlite3\n[a-f0-9]{64}  device-profile\n', seed)
          and raw(directory / 'expected-seed.sha256', 256) == seed, 'Fixture metadata or profile changed')
    if cache_regime == 'empty-cache-v1':
        check(raw(directory / 'cold-fixture') == b'empty-cache-v1\n' and
              line(directory / 'cold-fixture.sha256', HEX) == sha(seed).encode(), 'Invalid cold seed binding')
    else:
        check(cache_regime == 'warm-cache-v1' and not (directory / 'cold-fixture').exists(), 'Unknown warm fixture regime')
    return {'frame': frame, 'wake': wake, **toolbars, 'seed': seed, 'fixture': line(directory / 'fixture-state.sha256', HEX),
            'catalog': catalog, 'access_before': access_before, 'access_after': access_after,
            'defaults': all(defaults), 'input_ok': bool(active & resumed),
            'normal_exit': raw(directory/'ordinary-exit.tsv') == ack and matched[-1].endswith(b'status 0.'),
            'resumed': before['same_process_resumes'] < after['same_process_resumes'] and frame == wake,
            'notified': lines.count(b'aidoku: Catalog notification completed.') == 1}


def pair_evidence(baseline, candidate, qualification_frames, qualification_toolbars=None):
    check(all(baseline[k] == candidate[k] for k in ('fixture', 'seed', 'catalog')),
          'Default runs have different fixtures or native catalog entries')
    check(baseline['access_before'] < baseline['access_after'] <= candidate['access_before'] < candidate['access_after'],
          'Native recent-access sequence is inconsistent')
    check({s[k] for s in (baseline, candidate) for k in ('frame', 'wake')} == qualification_frames
          and len(qualification_frames) == 1, 'Default/restored Library differs from independently qualified pixels')
    keys = ('toolbar', 'wake_toolbar')
    if qualification_toolbars is not None or any(k in s for s in (baseline, candidate) for k in keys):
        check(isinstance(qualification_toolbars, (set, frozenset)) and len(qualification_toolbars) == 1 and
              all(s.get(k) is not None and s[k] in qualification_toolbars for s in (baseline, candidate) for k in keys),
              'Default/restored right status bar differs from independently qualified pixels')


def independent(default_run, screen_ids, confirm_ids, visual_ids):
    groups = [{default_run}, screen_ids, confirm_ids, visual_ids]
    check(all(groups) and sum(map(len, groups)) == len(set.union(*groups)), 'Qualification runs are not independent')


def archive_members(path, consume, file_limit):
    paths = set()
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            gate.safe_member(item.name)
            check(item.name not in paths, 'Duplicate archive member')
            paths.add(item.name)
            if item.isdir():
                continue
            check(item.isreg() and item.size <= file_limit and len(paths) < 2048, 'Invalid archive member')
            data = archive.extractfile(item).read()
            check(len(data) == item.size, 'Incomplete archive')
            consume(item.name, data)


def check_resources(batch, plan, program, cold):
    run = plan['run_id']
    runner, device = maker.runner_text(), maker.device_text(cold)
    for leaf, key, expected in (('runner.py', 'runner_sha256', runner), ('device.sh', 'device_script_sha256', device)):
        check(raw(batch / leaf) == expected and sha(expected) == plan[key], 'Lifecycle test harness identity differs')
    smoke = raw(HERE / 'smoke.sh')
    check(smoke.count(b'# Entry point.') == 1, 'Smoke library boundary differs')
    expected_bytes = {'owner': (run + '\n').encode(), 'bridge.sh': maker.bridge(run), 'lifecycle_device.sh': raw(HERE / 'lifecycle_device.sh'),
                      'bench-lib.sh': maker.bound(maker.WARM / 'bench_device.sh', maker.PIN['bench_device.sh']),
                      'smoke-lib.sh': smoke.split(b'# Entry point.')[0]}
    expected = {name: sha(b) for name, b in expected_bytes.items()}
    expected['capture'] = maker.CAPTURE_SHA
    original = raw(batch / 'original-launcher')
    check(sha(original) == gate.DEPLOYED[gate.LAUNCH] == plan['original_launcher_sha256'] and
          raw(batch / 'original-program-backup.stdout') == original, 'Original launcher backup differs')
    check(plan['owner_hash'] == expected['owner'] and plan['bridge_sha256'] == expected['bridge.sh'],
          'Lifecycle bridge ownership or behavior differs')
    for side in SIDES:
        side_program = gate.DEPLOYED if side == 'baseline' else program
        v = plan['variants'][side]
        check(gate.observed_artifacts(v) == side_program and v['args'] == [] and v['env'] == {}, 'Unexpected default invocation')
        check(v['variant_sha256'] == sha(json.dumps({k: x for k,x in v.items() if k != 'variant_sha256'},
                                                   sort_keys=True, separators=(',', ':')).encode()), 'Variant digest differs')
        expected[side + '/launch.sh'] = side_program[gate.LAUNCH]
        expected[side + '/binary-hash'] = sha((side_program[gate.APP] + '\n').encode())
        ref = 'installed' if side_program[gate.APP] == gate.DEPLOYED[gate.APP] else side_program[gate.APP]
        expected[side + '/binary-ref'] = sha((ref + '\n').encode())
        if ref != 'installed':
            expected['bin-' + ref] = ref
        if gate.EXTRA in side_program:
            expected[side + '/app/aidoku-startup-portrait'] = side_program[gate.EXTRA]
    payload = batch / 'payload.tar.gz'
    check(sha(raw(payload, 95 * 1024 * 1024)) == plan['payload_sha256'], 'Lifecycle payload changed')
    observed, manifest = {}, {}

    def item(name, data):
        if name != 'inputs.sha256':
            observed[name] = sha(data)
            return
        for row in data.splitlines():
            m = re.fullmatch(rb'([0-9a-f]{64})  ([a-zA-Z0-9_./-]+)', row)
            check(m and m[2].decode() not in manifest, 'Malformed lifecycle input checksums')
            manifest[m[2].decode()] = m[1].decode()
    archive_members(payload, item, 88 * 1024 * 1024)
    check(expected == observed == manifest, 'Manifest, staged programs and approved input bytes differ')


def check_operating_batch(batch, plan):
    labels = plan['cases']
    run = plan['run_id']
    report = gate.unique_json(raw(batch / 'report.json'))
    check(all(report.get(k) == v for k,v in plan.items()), 'Lifecycle result differs from the frozen plan')
    check(report.get('completed') == labels and report.get('case_exception') is None and all(
        report.get(k) is True for k in ('success', 'lease_released', 'same_boot', 'native_restored', 'entry_restored',
                                        'installed_and_user_unchanged', 'input_roster_unchanged', 'temporary_stage_absent', 'cases_completed')),
          'Lifecycle batch did not finish and restore under its own lease')
    names = [c.get('name') for c in report['commands']]
    normal = ['before', 'create-stage', 'upload', 'prepare-device', 'original-program-backup', 'activate-bridge',
              *['case-' + case for case in labels], 'owned-stage-status', 'repair-launcher', 'collect',
              'restore-light', 'after', 'remove-owned-stage']
    check(names == normal and all(type(c['returncode']) is int and c['returncode'] == 0 for c in report['commands']),
          'Default qualification had incomplete or repeated operations')
    for case in labels:
        check(raw(batch / ('case-' + case + '.stdout')) == b'LIFECYCLE_OK\n' and
              raw(batch / ('case-' + case + '.stderr')) == b'', 'Default launch did not finish cleanly')
    # restore_launcher consumes the child's LIFECYCLE_RECOVERED marker with
    # command substitution and an exact test. The outer command is silent on
    # success. Empty output is meaningful only with this exact frozen code,
    # its successful return, and the post-restoration observations below.
    device = maker.device_text(plan.get('cache_regime') == 'empty-cache-v1')
    runner = maker.runner_text()
    check(raw(batch/'device.sh') == device and sha(device) == plan['device_script_sha256']
          and raw(batch/'runner.py') == runner and sha(runner) == plan['runner_sha256'],
          'Recovery command is not bound to the reviewed transport and device scripts')
    check(raw(batch/'owned-stage-status.stdout') == b'owned\n' and raw(batch/'owned-stage-status.stderr') == b''
          and all(raw(batch/(name+suffix)) == b'' for name in ('repair-launcher','restore-light','remove-owned-stage')
                  for suffix in ('.stdout','.stderr')) and raw(batch/'after.stderr') == b'',
          'Unexpected output from an owned native restoration command')
    before = raw(batch / 'before.stdout')
    check(before == raw(batch / 'after.stdout'), 'Native and installed baseline changed')
    for path in ('/mnt/us/aidoku/data/library.sqlite3', '/mnt/us/aidoku/data/device-profile',
                 '/mnt/us/documents/Aidoku.sh', '/mnt/us/aidoku/launch.sh', gate.REGISTRATION.rsplit('/',1)[0] + '/protocol'):
        found = re.findall(rb'(?m)^([a-f0-9]{64})  ' + re.escape(path.encode()) + rb'$', before)
        check(len(found) == 1, 'Incomplete protected file snapshot')
        if path.endswith('/launch.sh'):
            check(found[0].decode() == gate.DEPLOYED[gate.LAUNCH], 'Launcher baseline differs')
        if path.endswith('/Aidoku.sh'):
            check(found[0] == b'709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960', 'Library document differs')
    check(re.search(rb'(?m)^event[0-9]+ ', before) and re.search(rb'(?m)^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$', before),
          'Missing input roster or boot identity')
    tail = before.splitlines()[-4:]
    check(len(tail) == 4 and tail[0] and b'shell_integration' not in tail[0] and tail[1] == b'active'
          and all(v.isdigit() and int(v) <= 24 for v in tail[2:]), 'Original native application or lighting state missing')
    acq, rel = [gate.unique_json(raw(batch / ('lease-' + name + '.json'))) for name in ('acquired','released')]
    check(all(p['run_id'] == run and p['owner_thread'] == run and p['ok'] is True for p in (acq, rel))
          and gate.digest(acq['lease_token_sha256']) == rel['lease_token_sha256']
          and all(rel.get(k) is True for k in ('owner_and_token_checked', 'native_restored_and_connections_closed',
                                                'lease_directory_absent_after_release')),
          'Lease acquisition and verified release do not match')
    stamp = lambda f: datetime.fromisoformat(f.replace('Z', '+00:00'))
    check(stamp(acq['acquired_at_utc']) <= stamp(rel['released_at_utc']), 'Lease time order differs')
    check(raw(batch / 'collect.stdout', 33 * 1024 * 1024) == raw(batch / 'outputs.tar.gz', 33 * 1024 * 1024),
          'Collected archive does not match the transport output')
    extracted = batch / 'outputs'; members = set()
    def item(name, data):
        members.add(name)
        check(name.startswith('out/') and raw(extracted / name) == data, 'Unsealed case observation')
    archive_members(batch / 'outputs.tar.gz', item, 1024 * 1024)
    check(members == {f.relative_to(extracted).as_posix() for f in extracted.rglob('*') if f.is_file()},
          'Extra or missing default observations')
    return bool(before) and before == raw(batch / 'after.stdout') and rel['native_restored_and_connections_closed'] is True


def reference_runs(report, workload, replay=False):
    """Use the approved physical evidence directories, never a path from a report."""
    status = workload == 'status'
    cold = workload == 'cold' or (status and report['workload'] == 'cold')
    root = COLD if cold else WARM
    phase = report['phase']
    check(phase in ('screen','confirm','qualify','pilot'), 'Invalid reference phase')
    ordinary, visual, seen = [], [], set()
    for batch in report['batches']:
        run = batch['run_id']
        check(re.fullmatch(r'aidoku-next-[a-z][a-z0-9-]{0,12}-[a-f0-9]{8}', run) and run not in seen,
              'Invalid or duplicate reference batch')
        seen.add(run)
        parent = root / 'private' / run
        check(parent.resolve(strict=True) == parent and parent.is_dir(), 'Unbound reference directory')
        check(sha(raw(parent/'plan.json')) == batch['frozen_plan_sha256'], 'Reference plan differs')
        if batch['mode'] == 'qualify':
            visual.append(parent)
        else:
            check(batch['mode'] == phase, 'Mixed reference stages')
            ordinary.append(parent)
    if replay:
        path = (Path('/root/aidoku-next-statusbench-20261118/tools/analyze_status.py') if status else
                root / 'tools' / ('analyze_cold.py' if cold else 'analyze_bench.py'))
        check(sha(raw(path)) == report['analyzer_sha256'], 'Qualification analyzer changed since reference capture')
        analyzer = load('_lifecycle_reference', path)
        if status:
            check(report['analyzer_sha256'] == gate.STATUS_ANALYZER, 'Unreviewed composite analyzer')
            rerun = analyzer.analyze(ordinary, visual, report['workload'], phase, gate.STATUS_APP, None)
        else:
            rerun = (analyzer.analyze(ordinary, visual, phase, None) if cold else
                     analyzer.analyze(ordinary, visual, phase, report['primary_endpoint'], None))
        check(rerun == report, 'Independent analysis no longer reproduces the evidence')
    return ordinary, visual


def cropped_reference(directory):
    pp = raw(directory / 'native-settled.ppm', 14_000_000)
    check(len(pp) == 13838436 and pp.startswith(b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'),
          'Independent physical image has a different format or geometry')
    return sha(pp[36 + 72 * 1860 * 3:]).encode()


def cropped_toolbar_reference(directory):
    meta = gate.unique_json(raw(directory / 'final.metadata.json'))
    width, height, mid, bar = 1860, 2480, 930, 72
    check(meta.get('backend') == 'kindle' and meta.get('profile') == 'scribe' and meta.get('route') == 'library'
          and meta.get('window_size') == [width, height] and meta.get('landscape') is False
          and type(meta.get('rotation_degrees')) is int and meta['rotation_degrees'] == 0,
          'Independent right status bar has a different presentation')
    header = b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'
    frame = raw(directory / 'native-settled.ppm', 14_000_000)
    check(len(frame) == len(header) + width * height * 3 and frame.startswith(header),
          'Independent right status bar has an invalid framebuffer format')
    pixels = memoryview(frame)[len(header):]
    rgb = b''.join(pixels[(row * width + mid) * 3:(row + 1) * width * 3] for row in range(bar))
    channels = [rgb[index::3] for index in range(3)]
    limits = tuple(v for channel in channels for v in (min(channel), max(channel)))
    check(any(lo < hi for lo, hi in zip(limits[::2], limits[1::2])), 'Independent right status bar is blank')
    return (*limits, sha(rgb.hex().encode('ascii')).encode('ascii'))


def source_evidence(batch, plan, source, selected):
    check(source.is_absolute() and source.resolve(strict=True) == source, 'Unbound release source')
    verifier = load('_lifecycle_qualified_source', gate.checked_path(source/'scripts/verify-kindle-package.py'))
    manifest = verifier.source_manifest(source)
    check(verifier.SOURCE_ALGORITHM == 'sha256-of-canonical-sorted-relative-file-records-v1' and
          dict(manifest, algorithm=verifier.SOURCE_ALGORITHM) == gate.unique_json(raw(batch/'qualified-source-manifest.json')) and
          manifest['sha256'] == plan['source_manifest_sha256'] == plan['source_manifest']['source_sha256'],
          'The current build source differs from the frozen source manifest')
    attestation_bytes = raw(batch / 'build-attestation.json')
    check(sha(attestation_bytes) == plan['source_manifest']['build_attestation_sha256'], 'Build attestation changed')
    attestation = gate.unique_json(attestation_bytes)
    check(attestation['artifact_sha256'] == selected and attestation['source_manifest_sha256'] == manifest['sha256'],
          'Build attestation is for other source or program bytes')
    evidence = attestation['evidence']
    check(isinstance(evidence, list) and 0 < len(evidence) <= 16, 'No original producer evidence')
    source_pair = False
    artifact_proof = set()
    for ref in evidence:
        data = gate.load_bound(ref)
        evidence_sha = sha(data)
        artifact_proof.update(k for k, v in selected.items() if v == evidence_sha or v.encode() in data)
        if len(data) <= 4 * 1024 * 1024:
            source_pair = source_pair or manifest['sha256'].encode() in data or any(
                e['path'].startswith(('src/', 'scripts/')) and e['path'].encode() in data and e['sha256'].encode() in data
                for e in manifest['files'])
    check(set(selected) == artifact_proof and source_pair,
          'Producer evidence is not bound to all selected artifacts and named release sources')
    return manifest['sha256']


def accepted_sources(batch, plan, selection, selected, qualification):
    status = 'status' in selection['candidates']
    cold = 'cold' in selection['candidates'] or status
    screens, confirms, warm_ids, seeds = set(), set(), set(), set()
    verified_refs = {}
    def replay_ref(label, ref):
        record = raw(batch / (label + '.json'), 12 * 1024 * 1024)
        check(sha(record) == ref['sha256'] == plan['reference_hashes'][label] and record == gate.load_bound(ref),
              'External and staged reference disagree')
        verified_refs[label] = sha(record)
        return gate.unique_json(record)
    entries = [(name, selection['candidates'][name], {k: selected[k] for k in gate.MEMBERS[name]},
                '' if len(selection['candidates']) == 1 else name + '_', False) for name in selection['candidates']]
    if len(entries) > 1:
        combo = selection['combination']
        entries.append((combo['primary'], combo, selected, '', True))
    for name, cfg, artifacts, prefix, combined in entries:
        identities = []
        for phase in ('screen', 'confirm'):
            report = replay_ref(prefix + phase, cfg[phase]); ids = gate.freeze_gate(report, phase, name, artifacts)
            gate.release_trial_context(report, artifacts, combined=combined)
            check(ids.isdisjoint(screens | confirms), 'Duplicate timing run')
            (screens if phase == 'screen' else confirms).update(ids)
            identities.append(report['artifacts'])
            timing, _ = reference_runs(report, name, replay=True)
            if cold:
                for folder in timing:
                    for row in next(b['cases'] for b in report['batches'] if b['run_id'] == folder.name):
                        seeds.add(raw(folder / 'outputs/out' / row['case'] / 'seed.sha256', 256))
        check(identities[0] == identities[1], 'Candidates differ between screening and confirmation')
        if name == 'status':
            warm = replay_ref('warm_guard', cfg['warm_guard'])
            warm_ids = gate.warm_status_guard(warm, artifacts)
            check(warm['artifacts'] == identities[0] and warm_ids.isdisjoint(screens | confirms),
                  'Warm guard is not a fresh comparison of the release pair')
            reference_runs(warm, name, replay=True)
    workload = entries[-1][0]
    record = replay_ref('pixels', selection['qualification'])
    check(raw(qualification, 12 * 1024 * 1024) == raw(batch/'pixels.json', 12 * 1024 * 1024)
          and verified_refs == plan['reference_hashes'] and verified_refs['pixels'] == plan['qualification_sha256'],
          'Default or pixel evidence references differ')
    check(record['valid'] is True and record['qualification_passed'] is True and
          gate.observed_artifacts(record['artifacts']['candidate']) == (gate.DEPLOYED | selected), 'Unqualified composition')
    gate.bind_workload(record, workload)
    _, visual = reference_runs(record, workload, replay=True)
    independent(plan['run_id'], screens, confirms, {f.name for f in visual})
    if status:
        check(gate.qualify_status(record) == {f.name for f in visual} and warm_ids
              and warm_ids.isdisjoint({plan['run_id'], *screens, *confirms, *(f.name for f in visual)}),
              'No independent cold toolbar qualification or matching warm guard')
    checks = record['pixels']
    check({f.name for f in visual} == {item['batch'] for item in checks} and all(
        x['identical_below_status_bar'] is True and (x['width'],x['height'],x['status_bar_excluded_pixels']) == (1860,2480,72)
        for x in checks), 'Missing independently qualified physical frame')
    frames, inputs, toolbars = set(), [], set()
    for f in visual:
        observations = [c for b in record['batches'] if b['run_id'] == f.name for c in b['cases'] if c['case'].endswith('-qualify')]
        check({c['case'] for c in observations} == {'baseline-qualify', 'candidate-qualify'}, 'Missing independent input observation')
        for c in observations:
            check(c['status'] == 'ok' and c['runner_gate_verified'] and c['operating_evidence_verified'], 'Invalid qualification case')
            sm = c['endpoints_ms']; inputs.append(sm['input_ready'] >= sm['first'] > 0)
            directory = f/'outputs/out'/c['case']
            frames.add(cropped_reference(directory))
            if status:
                toolbars.add(cropped_toolbar_reference(directory))
            if cold:
                check(c['cold']['fixture_verified'] and c['cold']['trace_verified'], 'Incomplete cold pixel or cache observation')
                seeds.add(raw(directory/'seed.sha256', 256))
    if cold:
        check(record['mechanism']['verified'] is True and record['artifacts'] == identities[-1] and len(seeds) == 1,
              'Cold reference workload, seed or observed mechanism differs')
    check(inputs and all(inputs), 'Input readiness is not established by the reference measurements')
    return frames, inputs, seeds, verified_refs, toolbars


def analyze(batch, qualification, source, pinned):
    check(batch.parent == HOST and batch.resolve(strict=True) == batch and batch.is_dir(), 'Unexpected private batch root')
    frozen = raw(batch/'plan.json')
    check(sha(frozen) == gate.digest(pinned), 'Lifecycle evidence does not match the pre-run plan')
    plan = gate.unique_json(frozen)
    run = plan['run_id']
    check(plan['mode'] == 'default-lifecycle' and plan['cases'] == CASES and run == batch.name and
          re.fullmatch(r'aidoku-next-life-[0-9a-f]{8}', run), 'Not an independent two-sided default lifecycle run')
    authoring = raw(batch/'authoring-selection.json'); selection = gate.unique_json(authoring)
    check(sha(authoring) == plan['selection_sha256'] and selection['schema'] == 1 and
          sorted(selection['candidates']) == plan['candidate_names'], 'Source selection changed')
    gate.allowed_changes(selection['candidates'], selection['artifacts'])
    selected = {name: sha(gate.load_bound(ref)) for name,ref in selection['artifacts'].items()}
    status = 'status' in selection['candidates']
    cold = 'cold' in selection['candidates'] or status
    workload = selection['combination']['primary'] if len(selection['candidates']) > 1 else next(iter(selection['candidates']))
    regime,policy = ('empty-cache-v1','per-case-empty-cache-v1') if cold else ('warm-cache-v1','per-side-seeded-warm-cache-v1')
    check(plan['program_sha256'] == selected and plan['primary'] == workload and (plan['cache_regime'],plan['fixture_policy']) == (regime,policy),
          'Lifecycle program selection or workload mismatch')
    source_sha = source_evidence(batch, plan, source, selected)
    check(source_sha == selection['source_manifest_sha256'], 'Different source for production defaults and measured candidate')
    if status:
        check(source_sha == gate.STATUS_SOURCE and selected == {gate.APP: gate.STATUS_APP},
              'Unqualified status/decode source or application')
    program = gate.DEPLOYED | selected
    check(sha(raw(source/'packaging/kindle/launch.sh')) == program[gate.LAUNCH], 'The actual release launcher differs')
    check_resources(batch, plan, program, cold)
    restored = check_operating_batch(batch, plan)
    frames, inputs, seeds, refs, toolbars = accepted_sources(batch, plan, selection, selected, qualification)
    cases = [case_evidence(batch/'outputs/out'/label, run, side, (gate.DEPLOYED if side == 'baseline' else program) |
                          {gate.JPEG: JPEG_SHA}, regime, check_toolbar=status) for label,side in zip(CASES,SIDES)]
    pair_evidence(*cases, frames, qualification_toolbars=toolbars if status else None)
    if cold:
        check(seeds == {c['seed'] for c in cases}, 'Default observations have a different cold seed')
    return {'schema': 1, 'method': 'Measured uninstrumented production defaults on isolated homes, verified EVIOCGRAB lifecycle, short powerd park/wake and cooperative TERM with ordinary-exit acknowledgement; physical touch and deep suspend are not claimed.',
            'run_id': run, 'candidates': plan['candidate_names'], 'qualification_plan_sha256': pinned,
            'source_manifest_sha256': source_sha, 'artifact_sha256': selected, 'reference_hashes': refs,
            'build_attestation_sha256': plan['source_manifest']['build_attestation_sha256'],
            'cache_regime': regime, 'fixture_policy': policy, 'mechanism': gate.MECHANISMS[workload],
            'analyzer_sha256': sha(raw(Path(__file__).resolve())),
            'checks': {'pixels': frames == {c['frame'] for c in cases}, 'input': all(inputs) and all(c['input_ok'] for c in cases),
                       'exit': all(c['normal_exit'] for c in cases), 'sleep_wake': all(c['resumed'] for c in cases),
                       'notification': all(c['notified'] for c in cases), 'catalog_identity': len({c['catalog'] for c in cases}) == 1,
                       'restoration': restored, 'production_defaults': all(c['defaults'] for c in cases),
                       **({'right_status_bar': len(toolbars) == 1 and all(c['toolbar'] in toolbars and c['wake_toolbar'] in toolbars
                                                                       for c in cases)} if status else {})}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('batch', 'qualification', 'source-root', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    args = parser.parse_args()
    check(sys.platform == 'linux', 'Run on test-env')
    result = analyze(args.batch, args.qualification, args.source_root, args.plan_sha256)
    check(all(result['checks'].get(k) is True for k in gate.LIFECYCLE), 'Lifecycle qualification did not pass')
    with args.output.open('x') as out:
        out.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'qualified': True, 'run_id': result['run_id']}))


if __name__ == '__main__':
    main()
