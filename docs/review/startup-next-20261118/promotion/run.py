#!/usr/bin/env python3
"""Install a frozen accepted delta, check the normal entry and restore on failure.

Run only on test-env, under the coordinating operator. The check-only mode
reads the frozen inputs without creating a lease. No catalog or registry writes
are issued by this driver; the application runs in its normal environment.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import uuid
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = Path('/root/aidoku-next-promotion-20261118')
LEASE_TOOLS = Path('/root/aidoku-display-modes-20261114-01a1da18/tools')
SEED = Path('/root/aidoku-startup-catalog-20261117/private/transport-seed.json')
CAPTURE = Path('/root/aidoku-native-query-20261117/tools/framebuffer-dump')
CAPTURE_SHA = 'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90'
APP, LAUNCH, EXTRA = 'aidoku/app/aidoku', 'aidoku/launch.sh', 'aidoku/app/aidoku-startup-portrait'
DEPLOYED = {
    APP: '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612',
    LAUNCH: '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296', EXTRA: 'absent',
}
DISPATCH = '2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86'
VERSION = '/var/local/kindle-catalog-entry/v1-' + DISPATCH
REGISTRATION = VERSION + '/aidoku-library-dispatch'
PROTECTED = {
    '/mnt/us/documents/Aidoku.sh': '709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960',
    '/mnt/us/aidoku/assets/aidoku-cover-v2.png': 'dde92cb1ae5f1dbf37db61e179a55522edea3759567fba74714f4954955dbf31',
    '/mnt/us/aidoku/assets/aidoku-cover.png': 'b3b65e42af19906ecc5f1c99a5b761b4bf4686bbc5a150a98bb4751f760090d9',
    '/mnt/us/aidoku/app/aidoku-native-query': '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d',
    '/mnt/us/aidoku/app/kindle-jpeg-worker': '1b41abcab8083ef70d3cb76ad16303d8b7934c382c4e49927f9789d11a7abf41',
    '/var/local/kmc/bin/sh_integration_launcher': '2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc',
    REGISTRATION: DISPATCH,
    VERSION + '/aidoku-entry-hooks.so': '448a68e9da0a80eb968f49c6d18ca93e99f1cae1a371866deffed9445d074667',
    VERSION + '/aidoku-catalog-client': 'f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161',
    '/usr/lib/ccat/change.lua': '971541da47ab5c3cafbde9ab80662d1b0055183c57aed9baf942a3360dbff5b4',
}
DIGEST = re.compile(r'[a-f0-9]{64}\Z')
ORIGINAL_RUN = re.compile(r'promote-[a-f0-9]{12}\Z')
q = shlex.quote


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def bound_file(path, expected, limit=90 * 1024 * 1024):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(), 'Invalid release input')
    require(DIGEST.fullmatch(expected) and path.stat().st_size <= limit, 'Invalid release input identity')
    content = path.read_bytes()
    require(sha(content) == expected, 'Release input hash differs')
    return content


def unique_json(raw):
    def pairs(items):
        result = dict(items)
        require(len(result) == len(items), 'Duplicate JSON field')
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def freeze(plan_path, expected):
    plan = unique_json(bound_file(plan_path, expected, 128 * 1024))
    require(plan.get('schema') == 1 and plan['catalog_registry'] == REGISTRATION, 'Not the deployed shared runtime')
    changes = plan['changes']
    require(isinstance(changes, list) and 1 <= len(changes) <= len(DEPLOYED), 'Invalid release change set')
    blobs, selected = {}, {}
    archive_path = plan_path.parent / 'aidoku-startup-next.zip'
    bound_file(archive_path, plan['archive_sha256'])
    bound_file(plan_path.parent / 'aidoku-startup-next.kpkg', plan['kpm_sha256'])
    with zipfile.ZipFile(archive_path) as archive:
        for row in changes:
            name, old, new = row['path'], row['old'], row['new']
            require(name in DEPLOYED and name not in selected and DEPLOYED[name] == old
                    and row['file'] == 'delta/' + name and new != old, 'Unqualified release target')
            require(name != APP or new != 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6',
                    'The earlier cold-only application cannot be deployed')
            data = bound_file(plan_path.parent / row['file'], new)
            require(archive.namelist().count(name) == 1 and archive.read(name) == data, 'ZIP member differs from delta')
            selected[name] = new
            blobs['payload/' + name] = (data, 0o600)
    require((LAUNCH in selected) == (EXTRA in selected), 'Partial supervisor release')
    require(set(plan['evidence_sha256']) >= {'pixels', 'lifecycle'} and all(
        DIGEST.fullmatch(value) for value in plan['evidence_sha256'].values()), 'Qualification evidence is missing')
    manifest = ''.join(f"{row['old']}|{row['new']}|{row['path']}\n" for row in changes).encode('ascii')
    blobs['changes.tsv'] = (manifest, 0o600)
    return blobs, DEPLOYED | selected


def stage_archive(plan_files, owner, install):
    files = dict(plan_files) if install else {'changes.tsv': plan_files['changes.tsv']}
    files['transaction.sh'] = ((HERE / 'transaction.sh').read_bytes(), 0o755)
    files['owner'] = ((owner + '\n').encode('ascii'), 0o600)
    if install:
        files['smoke.sh'] = ((HERE / 'smoke.sh').read_bytes(), 0o755)
        files['capture'] = (bound_file(CAPTURE, CAPTURE_SHA, 2 * 1024 * 1024), 0o755)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, (content, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.mode, info.size = mode, len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue(), files


def check_files(items):
    lines = []
    for path, digest in items.items():
        require(path.startswith(('/mnt/us/', '/usr/lib/', '/var/local/')) and '..' not in path, 'Invalid fixed path')
        lines += [f'test ! -L {q(path)}']
        if digest == 'absent':
            lines += [f'test ! -e {q(path)}']
        else:
            require(DIGEST.fullmatch(digest), 'Invalid expected SHA-256')
            lines += [f'test -f {q(path)}', f'v=$(sha256sum {q(path)}); test "${{v%% *}}" = {digest}']
    return '\n'.join(lines) + '\n'


def program(values):
    return check_files({'/mnt/us/' + path: value for path, value in values.items()})


REGISTRY = '''test -f /var/local/appreg.db && test ! -L /var/local/appreg.db
selected=$(sqlite3 -readonly -cmd '.timeout 1500' /var/local/appreg.db "SELECT value FROM properties WHERE handlerId='tech.hackerdude.shell_integration.launcher' AND name='command';")
test "$selected" = ''' + q(REGISTRATION) + '\n'


NATIVE = '''test ! -e /tmp/aidoku-launcher.lock && test ! -L /tmp/aidoku-launcher.lock
if ids=$(pidof aidoku 2>/dev/null); then exit 1; else rc=$?; test "$rc" = 1 && test -z "$ids"; fi
test "$(lipc-get-prop -s com.lab126.powerd state)" = active
case "$(status statusbar)" in *start/running*) ;; *) exit 1;; esac
wm=$(pidof awesome); test -n "$wm"
for p in $wm; do
 state=$(sed -n 's/^State:[[:space:]]*//p' "/proc/$p/status")
 case "$state" in S*|R*) ;; *) exit 1;; esac
done
ui=$(lipc-get-prop com.lab126.appmgrd activeApp); test -n "$ui"
case "$ui" in *shell_integration.launcher*) exit 1;; esac
'''
STATE = NATIVE + '''test "$(cat /mnt/us/aidoku/data/device-profile)" = scribe
printf '%s\\n' "$ui"; cat /proc/sys/kernel/random/boot_id
for n in /sys/class/input/event*; do
 printf '%s ' "${n##*/}"; cat "$n/dev" "$n/device/name" "$n/device/capabilities/abs" || exit 1
done
sha256sum /mnt/us/aidoku/data/device-profile
'''
LIGHT = '''lipc-get-prop -i com.lab126.powerd flIntensity
lipc-get-prop -i com.lab126.powerd currentAmberLevel
'''


class Transport:
    def __init__(self, run):
        self.run = run
        self.batch = ROOT / 'private' / run
        self.batch.mkdir(parents=True, mode=0o700)
        config = unique_json(SEED.read_bytes())
        self.config = self.batch / 'transport.json'
        owner = 'aidoku-next-' + run
        token = self.batch / 'lease-token'
        config.update(owner_thread=owner, lease_run_id=run, lease_token_file=str(token))
        self.config.write_text(json.dumps(config))
        self.config.chmod(0o600)
        sys.path.insert(0, str(LEASE_TOOLS))
        import device_lease
        self.api = device_lease
        self.request = self.api.LeaseRequest(Path(config['lease_dir']), token, owner, run)
        self.calls = []

    def connect(self):
        return self.api.acquire(self.request)

    def release(self):
        proof = self.api.release(self.request, native_restored_and_connections_closed=True)
        (self.batch / 'lease-released.json').write_text(json.dumps(proof, indent=2) + '\n')

    def call(self, name, script, stdin=None, timeout=30):
        if stdin is None:
            stdin = ('set -eu\numask 077\n' + script.rstrip() + '\n').encode('ascii')
            require(len(stdin) < 1024 * 1024, 'Unbounded device input')
            script = 'sh -s'
        proc = subprocess.Popen([sys.executable, '-I', str(LEASE_TOOLS / 'guarded-device-ssh.py'),
                                 '--config', str(self.config), script], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        expired = False
        try:
            out, err = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            expired = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                out, err = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                out, err = proc.communicate()
        seq = f'{len(self.calls):02d}-{name}'
        (self.batch / (seq + '.stderr')).write_bytes(err)
        (self.batch / (seq + '.stdout')).write_bytes(out)
        self.calls.append({'operation': name, 'exit': proc.returncode, 'timeout': expired})
        if expired or proc.returncode:
            raise RuntimeError(f'{name} failed, see private evidence')
        return out


def stage_guard(stage, transaction_run):
    return (f'test ! -L {q(stage)} && test "$(readlink -f {q(stage)})" = {q(stage)}\n'
            f'cd {q(stage)}\ntest -f owner && test ! -L owner\n'
            f'test "$(cat owner)" = {q(transaction_run)}\n')


def light_levels(raw):
    levels = raw.decode('ascii').splitlines()
    require(len(levels) == 2 and all(re.fullmatch(r'[0-9]{1,2}', item) and int(item) <= 24 for item in levels),
            'Unexpected light state')
    return levels


def restore_levels(session, expected):
    actual = light_levels(session.call('light-before-restore', LIGHT))
    for prop, incoming, target in zip(('flIntensity', 'currentAmberLevel'), actual, expected):
        if incoming != target:
            session.call('restore-' + prop, f'lipc-set-prop -i com.lab126.powerd {prop} {target}')
    require(light_levels(session.call('light-after-restore', LIGHT)) == expected, 'Lighting did not restore')


def matching_toolbar_observations(raw):
    rows = raw.decode('ascii').splitlines()
    require(len(rows) == 3 and len(set(rows)) == 1, 'Installed right toolbar differs between opens or after wake')
    values = rows[0].split('\t')
    require(len(values) == 12 and values[:5] == ['930','0','930','72','200880'] and DIGEST.fullmatch(values[-1]),
            'Right toolbar region, byte count or digest is invalid')
    require(all(v.isdigit() and len(v) <= 3 for v in values[5:11]), 'Invalid right toolbar color ranges')
    extrema = [int(v) for v in values[5:11]]
    channels = [(extrema[i],extrema[i+1]) for i in (0,2,4)]
    require(all(0 <= low <= high <= 255 for low,high in channels) and any(low < high for low,high in channels),
            'Right toolbar has no observable variation within a color channel')
    return True


def stage_cleanup(stage, tx, files):
    cmd = stage_guard(stage, tx)
    names = (set(files) - {'owner'}) | {
        'before.sha', 'after.sha', 'wake.sha', 'before.right.tsv', 'after.right.tsv', 'wake.right.tsv',
        'owned-app', 'owned-app.pending', 'smoke.request',
        'smoke.sleep', 'smoke.log.bound', 'smoke.native', 'smoke.catalog', 'smoke.access',
    }
    for path in sorted(names):
        cmd += f'test ! -L {q(path)}\nif [ -e {q(path)} ]; then test -f {q(path)}; rm -f -- {q(path)}; fi\n'
    for path in ('payload/aidoku/app', 'payload/aidoku', 'payload'):
        cmd += f'test ! -L {q(path)}\nif [ -d {q(path)} ]; then rmdir {q(path)}; fi\n'
    return cmd + f'test "$(ls -A .)" = owner\nrm -f -- owner\ncd /\nrmdir {q(stage)}\n'


def perform(session, payload, files, tx, updated, install):
    stage = '/mnt/us/.aidoku-next-' + session.run
    guard = stage_guard(stage, tx)
    backup = '/mnt/us/.aidoku-performance-backups/' + tx
    old, wanted = (DEPLOYED, updated) if install else (updated, DEPLOYED)
    protection = REGISTRY + check_files(PROTECTED)
    outcome = {'run_id': session.run, 'transaction': tx, 'success': False, 'lease_released': False}
    acquired = prior_state = original_light = None
    stage_attempted = stage_created = verified_stage = smoke_attempted = install_attempted = False
    failed = True
    try:
        proof = session.connect()
        acquired = True
        (session.batch / 'lease-acquired.json').write_text(json.dumps(proof, indent=2) + '\n')
        prior_state = session.call('native-before', STATE)
        session.call('protection-before', protection + program(old))
        original_light = light_levels(session.call('lighting-before', LIGHT))
        marker = f'printf "%s\\n" {q(tx)} >{q(stage + "/owner")}\n'
        stage_attempted = True
        session.call('create-stage', f'test ! -e {q(stage)} && test ! -L {q(stage)}\nmkdir -m 700 {q(stage)}\n' + marker)
        stage_created = True
        session.call('upload', 'set -eu; ' + guard.replace('\n', '; ') + 'tar -xzf -', stdin=payload, timeout=95)
        for key in ('transaction.sh', 'changes.tsv', *(['smoke.sh', 'capture'] if install else [])):
            digest = sha(files[key][0])
            guard += f'test ! -L {q(key)} && test -f {q(key)}; v=$(sha256sum {q(key)}); test "${{v%% *}}" = {digest}\n'
        session.call('verify-helpers', guard)
        verified_stage = True
        if install:
            smoke_attempted = True
            require(session.call('baseline-smoke', guard + f'sh ./smoke.sh before {q(tx)}', timeout=75) == b'BASELINE_SMOKE_OK\n',
                    'Baseline smoke was not certified')
            smoke_attempted = False
            require(session.call('baseline-restored', STATE) == prior_state, 'Baseline native state differs')
            session.call('pre-install-check', protection + program(old))
        install_attempted = install
        mode = 'install' if install else 'rollback'
        reply = session.call(mode, guard + f'sh ./transaction.sh {mode} {q(tx)}', timeout=100)
        require(reply == (b'INSTALLED\n' if install else b'ROLLED_BACK\n'), 'Unexpected transaction result')
        session.call('verify-program', protection + program(wanted))
        if install:
            smoke_attempted = True
            require(session.call('installed-smoke', guard + f'sh ./smoke.sh after {q(tx)}', timeout=105) == b'CANDIDATE_SMOKE_OK\n',
                    'Installed smoke was not certified')
            smoke_attempted = False
            data = session.call('right-toolbar-observations', guard + '''for f in before.right.tsv after.right.tsv wake.right.tsv; do
                test -f "$f" && test ! -L "$f" && test "$(wc -c <"$f")" -le 240; cat "$f"; done''')
            outcome['matching_observable_right_toolbar'] = matching_toolbar_observations(data)
        restore_levels(session, original_light)
        require(session.call('restored', STATE) == prior_state, 'Native or input state differs after use')
        session.call('protection-after', protection + program(wanted))
        outcome.update(qualified=install, backup_retained=True, native_and_lighting_restored=True,
                       original_shared_registration_preserved=True)
        if install:
            outcome.update(installed_default_document=True, same_process_short_sleep_wake=True,
                           pixels_match_before_after_and_wake=True, catalog_notifications_complete=True)
        failed = False
        outcome['success'] = True
    except Exception:
        outcome['failed_operation'] = session.calls[-1]['operation'] if session.calls else 'acquire'
    finally:
        if acquired:
            errors = []

            def cleanup(label, action):
                try:
                    action()
                    return True
                except Exception:
                    errors.append(label)
                    return False

            closed = True
            if stage_attempted and not stage_created:
                def check_stage():
                    nonlocal stage_created
                    script = f'if [ ! -e {q(stage)} ] && [ ! -L {q(stage)} ]; then printf "ABSENT\\n"; else\n'
                    result = session.call('stage-after-interruption', script + stage_guard(stage, tx) + 'printf "OWNED\\n"; fi')
                    require(result in (b'ABSENT\n', b'OWNED\n'), 'Unexpected stage state')
                    stage_created = result == b'OWNED\n'
                closed = cleanup('inspect-incomplete-stage', check_stage)
            if failed and verified_stage and smoke_attempted:
                closed = cleanup('recover-owned-application', lambda: require(
                    session.call('recover-owned-application', guard + f'sh ./smoke.sh recover {q(tx)}', timeout=75)
                    == b'RECOVERED\n', 'Recovery was not certified'))
            if failed and verified_stage and install_attempted and closed:
                def undo():
                    script = guard + f'if [ -f {q(backup + "/sealed")} ]; then sh ./transaction.sh rollback {q(tx)}; fi\n'
                    session.call('undo-program', script, timeout=100)
                    session.call('original-program-restored', program(old))
                closed = cleanup('rollback-files', undo) and closed
            if failed and verified_stage and not install and closed:
                # Retry only the journaled restoration. Mixed/foreign SHA values
                # are rejected before this transaction can write a single file.
                def retry_rollback():
                    session.call('finish-rollback', guard + f'sh ./transaction.sh rollback {q(tx)}', timeout=100)
                    session.call('original-program-restored', program(DEPLOYED))
                closed = cleanup('finish-rollback', retry_rollback) and closed
            if failed and stage_created and original_light is not None and closed:
                closed = cleanup('restore-lighting', lambda: restore_levels(session, original_light)) and closed
            if failed and stage_created and closed:
                # A concurrent shared-state change is evidence, never authority
                # to write that state back. Record failure but release after our
                # own application, files, native control and lighting are safe.
                cleanup('protected-files-after-recovery', lambda: session.call('protection-after-recovery', protection))
            if stage_created and prior_state is not None:
                closed = cleanup('final-native-check', lambda: require(session.call('final-native', STATE) == prior_state,
                                                                        'Original native state differs')) and closed
            if stage_created and closed:
                closed = cleanup('remove-stage', lambda: session.call('remove-stage', stage_cleanup(stage, tx, files))) and closed
            if closed:
                outcome['lease_released'] = cleanup('release', session.release)
            if errors:
                outcome['success'] = False
                outcome['recovery_errors'] = errors
            if not closed:
                outcome['recovery_needed'] = True
        (session.batch / 'report.json').write_text(json.dumps(outcome | {'calls': session.calls}, indent=2) + '\n')
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--operation', choices=('install-smoke', 'rollback'), required=True)
    parser.add_argument('--transaction-run', help='Original recorded promotion run, required for rollback')
    parser.add_argument('--check-only', action='store_true', help='Check frozen host inputs without taking a lease')
    args = parser.parse_args()
    require(sys.platform.startswith('linux') and sys.flags.optimize == 0, 'Run on test-env with Python checks enabled')
    os.umask(0o077)
    install = args.operation == 'install-smoke'
    require((install and args.transaction_run is None) or (not install and ORIGINAL_RUN.fullmatch(args.transaction_run or '')),
            'Invalid original transaction identity')
    run = ('promote-' if install else 'undo-') + uuid.uuid4().hex[:12]
    tx = run if install else args.transaction_run
    files, updated = freeze(args.plan, args.plan_sha256)
    payload, members = stage_archive(files, tx, install)
    for name in ('transaction.sh', 'smoke.sh'):
        subprocess.run(['sh', '-n', str(HERE / name)], check=True)
    if args.check_only:
        print(json.dumps({'prepared': True, 'mode': args.operation, 'changed_files': len(files) - 1,
                          'device_accessed': False, 'lease_acquired': False}))
        return
    result = perform(Transport(run), payload, members, tx, updated, install)
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result['success'] and result['lease_released'] else 1)


if __name__ == '__main__':
    main()
