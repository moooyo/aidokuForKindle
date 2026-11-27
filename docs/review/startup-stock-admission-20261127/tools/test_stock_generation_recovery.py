#!/usr/bin/env python3
"""Run the integrated shell and host protocol against private Linux fixtures."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
RUN = runpy.run_path(str(HERE/'run_stock_generation.py'))
PREPARE = runpy.run_path(str(HERE/'prepare_stock_generation.py'))
BASE = runpy.run_path(str(PREPARE['private_path']('base_runner')))
BOOT = '11111111-2222-3333-4444-555555555555'
LABEL = '0123456789abcdef'
VIEW = b'library 10 20\n'
HOME = b'home 120 240\n'


def replace(data, old, new):
    if data.count(old) != 1:
        raise AssertionError(f'Missing unique fixture anchor: {old}')
    return data.replace(old, new, 1)


def write(path, data, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if executable:
        path.chmod(0o700)


class DeviceModel:
    def __init__(self, root, kind):
        self.root, self.kind = root, kind
        self.media = root/'media'
        self.state = root/f'var/aidoku-native-{LABEL}'
        self.stage = self.media/f'.aidoku-native-{LABEL}'
        self.home = self.media/'aidoku'
        self.log = self.home/'data/logs/launcher.log'
        self.boot = root/'boot'
        self.fake_readonly = root/'readonly'
        self.launcher_lock = root/'launcher.lock'
        self.events = root/'events'
        self.view = root/'native-view'
        self.view.write_bytes(VIEW)
        self.payload = root/'synthetic-payload'
        self.payload.write_bytes(b'private test; never a real stock payload\n')
        root.joinpath('var').mkdir()
        self.boot.write_text(BOOT+'\n')
        self.log.parent.mkdir(parents=True)
        write(self.home/'data/device-profile', b'scribe\n')
        write(self.home/'app/aidoku', b'fixture app\n', True)
        write(self.home/'app/aidoku-native-query', b'official query\n', True)
        write(self.media/'kmc/kpm/packages/aidoku/payload/aidoku/app/aidoku-native-query', b'official query\n', True)
        package = self.media/'kmc/kpm/packages/aidoku'
        for leaf, value in (('payload.tsv',b'new package rows\n'), ('.aidoku-installation-complete',b'completion\n')):
            write(package/leaf,value)
        self.script = root/'fixture-device.sh'
        source = (HERE/f'{kind}-template.sh').read_text()
        source = replace(source, 'media=/mnt/us\n', f'media={shlex.quote(str(self.media))}\n')
        source = replace(source, 'state=/var/tmp/aidoku-native-$label\n', f'state={root}/var/aidoku-native-$label\n')
        source = replace(source, 'lock=/tmp/aidoku-launcher.lock\n', f'lock={self.launcher_lock}\n')
        source = replace(source, 'boot=/proc/sys/kernel/random/boot_id\n', f'boot={self.boot}\n')
        source = replace(source, 'directory /var/tmp || admission_reason paths_unverified',
                         f'directory {root}/var || admission_reason paths_unverified')
        source = replace(source, 'set -efu\n', 'set -efu\nmodel='+shlex.quote(str(root))+'\n')
        mocks = r'''
# Only the device calls are mocked. Journal, shell verbs, boot files, flock,
# pending writes, reference checks, tar and the timing parsers are real Linux.
native() {
    if [ -e "$model/native_busy" ]; then
        if [ "${1:-}" = admission ]; then printf 'native_foreground_unverified\n' >&8; fi
        return 1
    fi
    printf 'fixture-native\n'
}
no_overlay() { [ ! -e "$model/foreign_mount" ] && [ ! -e "$model/bound_mount" ]; }
no_owned_mount() { [ ! -e "$model/bound_mount" ]; }
stock_original() { same_boot && native >/dev/null; }
wait_stock() { stock_original; }
lights() { [ ! -e "$model/lighting_failure" ]; }
get_light() { printf '10\n'; }
catalog() { printf 'fixture-catalog\n'; }
stock_dispatcher() { printf 'fixture-dispatcher\n'; }
entry_preserved() { [ ! -e "$model/catalog_changed" ]; }
original_stamp() { printf 'fixture-original\n'; }
installed_program_matches() { :; }
usleep() { sleep 0.001; }
'''
        # The real warm-up reference() stays in the generated script to exercise
        # both query copies. Other production leaves are not needed by the model.
        source = replace(source, 'stock_admit() (\n', mocks+'\nstock_admit() (\n')
        tools = ('flock usleep cmp stat readlink gzip base64 sqlite3 mount umount timeout' if kind == 'measure'
                 else 'flock usleep cmp stat readlink gzip base64 sqlite3')
        source = replace(source, 'for tool in '+tools+';', 'for tool in flock cmp stat readlink gzip base64 tar'+
                         (' timeout;' if kind == 'measure' else ';'))
        if kind == 'measure':
            # Keep the real recovery state machine, timeout, and lock. Replace
            # just the ARM endpoints with hardware effects in a private model.
            self.input_kit = root/'fixture-input'
            for leaf, content in (
                ('navigation.sh', r'''#!/bin/sh
set -eu
model=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
[ "$1" = inspect ] && [ ! -e "$model/buttons_held" ] || exit 1
exec cat "$model/native-view"
'''),
                ('lib/ld-linux-armhf.so.3', r'''#!/bin/sh
[ "$1" = --inhibit-cache ] && [ "$2" = --library-path ] || exit 1
shift 3
exec "$@"
'''),
                ('card-input', r'''#!/bin/sh
set -eu
model=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
[ ! -e "$model/bound_mount" ] && [ ! -e "$model/foreign_mount" ] && [ ! -e "$model/buttons_held" ] || exit 1
read -r tab xy <"$model/native-view"
case "$1:$tab" in
    nav-library:home)
        printf 'nav-library\n' >>"$model/events"
        if [ -e "$model/hold-navigation" ]; then
            for n in $(seq 1 140); do [ ! -e "$model/hold-navigation" ] && break; sleep 0.03; done
            [ ! -e "$model/hold-navigation" ] || exit 1
        fi
        [ -e "$model/nav_no_effect" ] || printf 'library 600 2410\n' >"$model/native-view";;
    restore-pointer:library)
        printf 'restore-pointer\n' >>"$model/events"
        printf 'library %s %s\n' "$2" "$3" >"$model/native-view"
        [ ! -e "$model/pointer_effect_no_reply" ] || exit 9;;
    *) exit 1;;
esac
'''),
            ):
                write(self.input_kit/leaf, content.encode(), True)
            model = r'''
bind_launcher() { : >"$model/bound_mount"; }
restore_launcher() {
    [ ! -e "$model/foreign_mount" ] || return 1
    if [ -e "$model/bound_mount" ]; then rm "$model/bound_mount"; printf 'unmounted\n' >>"$model/events"; fi
}
visit_process_finished() { [ ! -e "$model/worker_still_running" ]; }
await_own() {
    [ -f "$model/model-worker-bind" ] || return 1
    record_visit_log "$state/out/control-01" "$(inode "$log")" || return 1
    printf '77 4000 42 3000 %s 51 2900\n' 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
}
stop_owned() {
    out=$(case_path) || return 1
    : >"$out/term"
    printf 'aidoku: Launcher worker exited with status 0.\n' >>"$log"
    rm -f "$model/native_busy"
}
model_one_slot() {
    out=$state/out/control-01; mkdir "$out"
    put "$out/origin" stock-tap && put "$state/current" control-01 && put "$state/active-slot" control-01
    register_snapshot >"$out/log-before"
    if [ -e "$model/before_tap" ]; then fail simulated_disconnect_before_tap; fi
    put "$state/tap-intent" control-01 && put "$out/dispatched" stock-tap
    printf 'one click\n' >>"$model/clicks"
    if [ -e "$model/queued" ]; then fail simulated_disconnect_after_tap; fi
    printf 'aidoku: Launcher worker started (pid=42).\n' >>"$log"
    put "$model/model-worker-bind" yes
    tuple=$(await_own 100) && remember_visit "$out" "$tuple"
    stop_owned "$tuple"
    if [ -f "$model/after-open-view" ]; then cp "$model/after-open-view" "$model/native-view"; fi
    rm "$state/current" "$state/active-slot"
}
'''
            source = replace(source, 'stock_admit() (\n', model+'\nstock_admit() (\n')
            source = replace(source, '\nrun_slot control-01\n', '\nmodel_one_slot\n')
            source = replace(source, '    close_owned repair || {',
                             '    [ ! -e "$model/skip_run_close" ] || exit 9\n    close_owned repair || {')
        body = r'''for rel in $refs; do
    if [ ! -e "$media/$rel" ]; then mkdir -p "$(dirname "$media/$rel")"; printf 'fixture\n' >"$media/$rel"; chmod 700 "$media/$rel"; fi
done
for rel in $small_refs; do
    mkdir -p "$(dirname "$stage/reference/$rel")"; cp "$media/$rel" "$stage/reference/$rel"
done
mkdir -p "$stage/reference/kpm" "$stage/input" "$pkg/payload/aidoku/app"
if absent "$pkg/payload/aidoku/app/aidoku"; then cp "$app" "$pkg/payload/aidoku/app/aidoku"; fi
cp "$pkg/payload.tsv" "$stage/reference/kpm/payload.tsv"
cp "$pkg/.aidoku-installation-complete" "$stage/reference/kpm/.aidoku-installation-complete"
stat -c %s "$app" >"$stage/reference/elf-size"
printf 'official query\n' >"$stage/reference/query.elf"
if [ -d "$model/fixture-input" ]; then
    cp -R "$model/fixture-input/." "$stage/input/"
else
    printf '#!/bin/sh\nprintf "library 10 20\\n"\n' >"$stage/input/navigation.sh"
fi
printf '#!/bin/sh\nprintf "fixture\\n" >"$2"\n' >"$stage/input/framewatch"
cp "$stage/input/framewatch" "$stage/input/tile-input"
printf '#!/bin/sh\nprintf "100\\n"\n' >"$stage/clock"
chmod 700 "$stage/clock" "$stage/input/framewatch" "$stage/input/tile-input"
printf '%s\n' "$kind" >"$stage/plan-kind"
if [ "$kind" = measure ]; then printf 'trace fixture\n' >"$stage/trace-launch.sh"; fi'''
        source = replace(source, 'tar -xzf - -C "$stage" || fail stage_upload', body)
        # Some fixtures choose to park after opening the real FD but before flock.
        gate = 'exec 9<"$state"; flock -n 9 || fail operation_running\ncurrent_boot_is && stock_run_may_stage'
        source = replace(source, gate, '''exec 9<"$state"
if [ "${FIXTURE_DELAY:-}" = 1 ]; then
    : >"$model/delayed_open"
    for n in $(seq 1 150); do [ ! -e "$model/resume" ] || break; sleep 0.03; done
    [ -e "$model/resume" ] || exit 22
fi
flock -n 9 || fail operation_running
current_boot_is && stock_run_may_stage''')
        self.script.write_text(source)

    def argv(self, action, boot=BOOT):
        return ['sh',str(self.script),action,LABEL,'stock','500',boot if action != 'admit' else '-']

    def call(self, action, boot=BOOT):
        return subprocess.run(self.argv(action, boot), capture_output=True, timeout=12)

    def expect(self, action, marker, boot=BOOT):
        p = self.call(action, boot)
        self.assert_reply(p, marker, action)
        return p

    def assert_reply(self, p, marker, action):
        expected = f'AB_{marker} {self.kind} {LABEL}'+(f' {BOOT}' if action in ('admit','readback') else '')
        if p.returncode != 0 or p.stdout.splitlines()[-1:] != [expected.encode()]:
            raise AssertionError((action,p.returncode,p.stdout,p.stderr))

    def inputs(self):
        return [line for line in self.events.read_text().splitlines() if line != 'unmounted'] if self.events.exists() else []


class DeviceFaults(unittest.TestCase):
    def fixture(self, kind):
        tmp = tempfile.TemporaryDirectory(prefix='stock-gen-device-')
        self.addCleanup(tmp.cleanup)
        return DeviceModel(Path(tmp.name), kind)

    def test_opening_fixture_has_scope_checked_before_unmount(self):
        source = (HERE/'measure-template.sh').read_text()
        run = source[source.index('run_slot() {'):source.index('\nno_owned_mount()')]
        self.assertIn('finish_one_owned_tap || fail click_completion_unverified\n    restore_launcher',run)
        with tempfile.TemporaryDirectory(prefix='stock-live-birth-') as temp:
            snippet = (HERE/'stock_tap_recovery.sh').read_text()
            preamble = '''absent() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }
'''
            path = Path(temp)/'process.sh'
            path.write_text(preamble+snippet+'\nvisit_process_finished "$1" "$2"\n')
            with subprocess.Popen(['sleep','8']) as sibling:
                try:
                    stat = Path(f'/proc/{sibling.pid}/stat').read_text().rsplit(') ',1)[1].split()
                    birth = int(stat[19])
                    cmd = ['sh',str(path),str(sibling.pid)]
                    self.assertEqual(subprocess.run([*cmd,str(birth)],check=False).returncode,1)
                    self.assertEqual(subprocess.run([*cmd,str(birth+1)],check=False).returncode,0)
                finally:
                    sibling.terminate()
                    sibling.wait(timeout=3)
            self.assertEqual(subprocess.run([*cmd,str(birth)],check=False).returncode,0)

    def test_locator_never_has_click_mount_or_navigation_mutation(self):
        d = self.fixture('locator')
        original = (HERE/'locator-template.sh').read_text()
        self.assertNotIn('input/dispatch.sh', original)
        self.assertNotIn('mount -o bind', original)
        self.assertNotIn('navigation.sh" library ', original)
        self.assertNotIn('navigation.sh" restore ', original)
        d.expect('admit', 'ADMITTED')
        d.expect('reserve', 'RESERVED')
        d.expect('run', 'RESTORED')
        for verb in ('readback', 'confirm-close'):
            with self.subTest(verb=verb):
                refused = d.call(verb)
                self.assertEqual((refused.returncode, refused.stderr), (1, b'AB_REFUSED arguments\n'))
        self.assertEqual(d.inputs(), [])
        self.assertTrue((d.state/'closed').is_file())
        self.assertTrue(d.stage.is_dir())
        d.expect('close', 'RESTORED')
        d.expect('finalize', 'FINALIZED')
        d.expect('absent', 'ABSENT')

    def test_delayed_actual_run_cannot_use_closed_removed_or_replaced_fd(self):
        for kind in ('locator', 'measure'):
            for state in ('closed', 'removed', 'replaced'):
                with self.subTest(kind=kind,state=state):
                    d = self.fixture(kind)
                    d.expect('reserve','RESERVED')
                    with subprocess.Popen(d.argv('run'), env=dict(os.environ, FIXTURE_DELAY='1'),
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as behind:
                        try:
                            limit = time.monotonic()+4
                            while not (d.root/'delayed_open').exists() and time.monotonic() < limit:
                                time.sleep(.02)
                            self.assertTrue((d.root/'delayed_open').is_file())
                            d.expect('close','RESTORED')
                            if state != 'closed':
                                d.expect('finalize','FINALIZED')
                                if state == 'replaced':
                                    d.expect('reserve','RESERVED')
                            (d.root/'resume').touch()
                            out, err = behind.communicate(timeout=9)
                            self.assertEqual((behind.returncode,out),(1,b''),err)
                            self.assertIn(b'run_not_admitted',err)
                            self.assertFalse(d.stage.exists())
                            self.assertFalse((d.root/'clicks').exists())
                        finally:
                            if behind.poll() is None:
                                (d.root/'resume').touch(exist_ok=True)
                                behind.communicate(timeout=9)

    def test_empty_or_new_boot_never_proves_mutable_operation_restored(self):
        d = self.fixture('measure')
        self.assertNotEqual(d.call('close').returncode,0)
        self.assertNotEqual(d.call('run').returncode,0)
        self.assertFalse(d.state.exists())
        d.expect('reserve','RESERVED')
        d.boot.write_text('11111111-2222-3333-4444-666666666666\n')
        for op in ('run','close','finalize','absent'):
            p=d.call(op)
            self.assertNotEqual(p.returncode,0)
            self.assertIn(b'different_boot',p.stderr)
        self.assertFalse(d.stage.exists())

    def test_no_tap_can_restore_and_unmount_without_a_second_stimulus(self):
        d = self.fixture('measure')
        (d.root/'before_tap').touch()
        d.expect('reserve','RESERVED')
        p=d.call('run')
        self.assertEqual(p.returncode,1,p.stderr)
        self.assertTrue(p.stdout.endswith(f'AB_RESTORED measure {LABEL}\n'.encode()))
        self.assertFalse((d.root/'clicks').exists())
        self.assertFalse((d.root/'bound_mount').exists())
        d.expect('close','RESTORED')
        d.expect('finalize','FINALIZED')
        d.expect('absent','ABSENT')

    def test_click_with_no_bound_worker_does_not_finalize_quiet_device(self):
        d = self.fixture('measure')
        (d.root/'queued').touch()
        d.expect('reserve','RESERVED')
        p=d.call('run')
        self.assertEqual(p.returncode,1,p.stderr)
        self.assertNotIn(b'AB_RESTORED',p.stdout)
        self.assertTrue((d.root/'bound_mount').exists())
        self.assertEqual((d.root/'clicks').read_text(),'one click\n')
        self.assertFalse((d.state/'closed').exists())
        self.assertNotEqual(d.call('close').returncode,0)
        self.assertNotEqual(d.call('finalize').returncode,0)
        self.assertNotEqual(d.call('absent').returncode,0)
        self.assertNotEqual(d.call('run').returncode,0)
        self.assertEqual((d.root/'clicks').read_text(),'one click\n')

    def test_closed_click_must_match_the_bound_log_inode_and_worker(self):
        for fault in ('copy', 'other_worker', 'still_running', 'valid'):
            with self.subTest(fault=fault):
                d = self.fixture('measure')
                (d.root/'queued').touch()
                d.expect('reserve','RESERVED')
                self.assertNotIn(b'RESTORED',d.call('run').stdout)
                out = d.state/'out/control-01'
                write(d.log,b'aidoku: Launcher worker started (pid=42).\naidoku: Launcher worker exited with status 0.\n')
                s = d.log.stat()
                write(out/'bound-log-inode',f'{s.st_dev}:{s.st_ino}\n'.encode())
                write(out/'bound-tuple',b'77 4000 42 3000 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 51 2900\n')
                if fault == 'copy':
                    d.log.rename(d.log.with_suffix('.moved'))
                    write(d.log,d.log.with_suffix('.moved').read_bytes())
                if fault == 'other_worker':
                    d.log.write_bytes(d.log.read_bytes().replace(b'pid=42',b'pid=49'))
                if fault == 'still_running':
                    (d.root/'worker_still_running').touch()
                p=d.call('close')
                self.assertEqual(p.returncode,0 if fault == 'valid' else 1,p.stderr)
                self.assertEqual((d.root/'bound_mount').exists(), fault != 'valid')

    def test_home_restore_is_one_tab_pair_then_one_pointer_motion_after_unmount(self):
        d = self.fixture('measure')
        write(d.root/'after-open-view', HOME)
        d.expect('reserve', 'RESERVED')
        d.expect('run', 'RESTORED')
        self.assertEqual(d.events.read_text().splitlines(), ['unmounted', 'nav-library', 'restore-pointer'])
        self.assertEqual(d.view.read_bytes(), VIEW)
        self.assertEqual((d.state/'input-restore-intent').read_text(), 'home library 10 20\n')
        d.expect('readback', 'READBACK_CLOSED')
        d.expect('close', 'RESTORED')
        d.expect('confirm-close', 'RESTORED')
        d.expect('finalize', 'FINALIZED')
        d.expect('absent', 'ABSENT')
        self.assertEqual(d.inputs(), ['nav-library', 'restore-pointer'])
        self.assertEqual((d.root/'clicks').read_text(), 'one click\n')

    def test_library_pointer_only_and_exact_need_no_tab_input(self):
        for view, expected in ((b'library 11 20\n', ['restore-pointer']), (VIEW, [])):
            with self.subTest(view=view):
                d = self.fixture('measure')
                write(d.root/'after-open-view', view)
                d.expect('reserve', 'RESERVED')
                d.expect('run', 'RESTORED')
                self.assertEqual(d.view.read_bytes(), VIEW)
                self.assertEqual(d.inputs(), expected)

    def test_unreadable_footers_buttons_and_coordinates_never_admit_input(self):
        for view, flag in ((b'unknown\n', None), (b'library 7000 20\n', None),
                           (b'home 10 20\n\n', None), (b'home 10 20', None), (HOME, 'buttons_held'),
                           (HOME, 'catalog_changed'), (HOME, 'lighting_failure')):
            with self.subTest(view=view, flag=flag):
                d = self.fixture('measure')
                write(d.root/'after-open-view', view)
                write(d.root/'skip_run_close', b'fixture\n')
                d.expect('reserve', 'RESERVED')
                self.assertEqual(d.call('run').returncode, 9)
                if flag:
                    (d.root/flag).touch()
                self.assertEqual(d.call('confirm-close').returncode, 1)
                self.assertEqual(d.call('close').returncode, 1)
                self.assertEqual(d.inputs(), [])
                self.assertFalse((d.state/'input-restore-intent').exists())
                self.assertFalse((d.state/'closed').exists())
                self.assertEqual(d.call('finalize').returncode, 1)

    def test_exact_readback_creates_no_journal_proof_and_rechecks_before_confirm(self):
        d = self.fixture('measure')
        write(d.root/'after-open-view', HOME)
        (d.root/'pointer_effect_no_reply').touch()
        d.expect('reserve', 'RESERVED')
        self.assertEqual(d.call('run').returncode, 1)
        self.assertEqual(d.view.read_bytes(), VIEW)
        d.expect('readback', 'READBACK_EXACT')
        self.assertFalse((d.state/'closed').exists())
        self.assertEqual(d.call('finalize').returncode, 1)
        for wrong in (HOME, b'library 600 2410\n'):
            d.view.write_bytes(wrong)
            self.assertEqual(d.call('confirm-close').returncode, 1)
            self.assertEqual(d.call('readback').returncode, 1)
            self.assertEqual(d.call('close').returncode, 1)
        d.view.write_bytes(VIEW)
        d.expect('confirm-close', 'RESTORED')
        d.expect('readback', 'READBACK_CLOSED')
        self.assertEqual(d.inputs(), ['nav-library', 'restore-pointer'])

    def test_held_input_prevents_readback_confirmation_and_cleanup(self):
        d = self.fixture('measure')
        write(d.root/'after-open-view', HOME)
        hold = d.root/'hold-navigation'
        hold.touch()
        d.expect('reserve', 'RESERVED')
        with subprocess.Popen(d.argv('run'), stdout=subprocess.PIPE, stderr=subprocess.PIPE) as active:
            try:
                limit = time.monotonic()+5
                while 'nav-library' not in d.inputs() and time.monotonic() < limit:
                    time.sleep(.02)
                self.assertEqual(d.inputs(), ['nav-library'])
                self.assertTrue((d.state/'input-restore-intent').exists())
                for verb in ('readback', 'confirm-close', 'close', 'finalize'):
                    p = d.call(verb)
                    self.assertEqual((p.returncode, p.stderr), (1, b'AB_REFUSED operation_running\n'))
            finally:
                hold.unlink(missing_ok=True)
                out, err = active.communicate(timeout=10)
        self.assertEqual(active.returncode, 0, err)
        self.assertIn(b'AB_RESTORED', out)
        self.assertEqual(d.inputs(), ['nav-library', 'restore-pointer'])

    def test_readback_rejects_wrong_boot_and_owner_even_if_navigation_looks_exact(self):
        d = self.fixture('measure')
        d.expect('reserve', 'RESERVED')
        d.expect('run', 'RESTORED')
        other_boot = BOOT[:-1]+'9'
        self.assertIn(b'different_boot', d.call('readback', other_boot).stderr)
        (d.state/'owner').write_text('another-attempt\n')
        for action in ('readback', 'confirm-close', 'close', 'finalize'):
            self.assertIn(b'no_owned_journal', d.call(action).stderr)
        self.assertEqual(d.inputs(), [])

    def test_both_query_copies_must_match_even_at_the_same_length(self):
        for rel in ('aidoku/app/aidoku-native-query','kmc/kpm/packages/aidoku/payload/aidoku/app/aidoku-native-query'):
            with self.subTest(relative_copy=rel):
                d = self.fixture('locator')
                bad = d.media/rel
                bad.write_bytes(b'foreign query!\n')
                self.assertEqual(len(bad.read_bytes()), len(b'official query\n'))
                d.expect('reserve','RESERVED')
                p = d.call('run')
                self.assertEqual(p.returncode,1,p.stderr)
                self.assertIn(b'installed_reference',p.stderr)
                self.assertTrue(p.stdout.endswith(f'AB_RESTORED locator {LABEL}\n'.encode()))
                self.assertFalse((d.state/'armed').exists())
                d.expect('finalize','FINALIZED')


class HostFaults(unittest.TestCase):
    def fixture(self, kind, faults=(), *, timeout=800, replies=None):
        tmp = tempfile.TemporaryDirectory(prefix='stock-gen-host-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        d = DeviceModel(root, kind)
        attempts = root/'host-private'
        attempts.mkdir(mode=0o700)
        attempt = attempts/f'attempt-{LABEL}'
        attempt.mkdir(mode=0o700)
        lease = BASE['load_lease']()
        base = BASE | {'LEASE': root/'fixture-lease'}
        self.assertNotEqual(base['LEASE'], BASE['LEASE'])
        sent, decode_count = [], []
        faults = set(faults)
        if 'partial_acquire_owned' in faults or 'partial_acquire_unverified' in faults:
            acquire = lease.acquire
            def partial(request):
                if 'partial_acquire_owned' in faults:
                    acquire(request)
                else:
                    base['LEASE'].mkdir(mode=0o700)
                raise lease.LeaseError('fixture_partial', 'Private fixture failure', lease_directory_created=True)
            lease.acquire = partial
        if 'other_plan_finishes_before_acquire' in faults:
            acquire = lease.acquire
            def finish_other_plan(request):
                # Model a previous dispatch having written its durable intent
                # and completed before this contender wins the shared lock.
                RUN['save'](attempts/f'{kind}-sent.json', {'attempt':'attempt-'+'a'*16, 'kind':kind})
                return acquire(request)
            lease.acquire = finish_other_plan
        if 'foreign_lease_busy' in faults:
            other = lease.LeaseRequest(base['LEASE'], root/'foreign-owner.token', 'fixture-other', 'fixture-other')
            lease.acquire(other)
            self.addCleanup(lambda: lease.release(other, native_restored_and_connections_closed=True))
        if faults & {'invalid_release_receipt', 'release_interrupted'}:
            release = lease.release
            def release_with_fixture_fault(*args, **kwargs):
                if 'release_interrupted' in faults:
                    raise OSError('Fixture release interrupted before mutating the lease')
                return release(*args, **kwargs) | ({'owner_and_token_checked': False} if 'invalid_release_receipt' in faults else {})
            lease.release = release_with_fixture_fault
        if 'queued' in faults:
            (root/'queued').touch()

        def factory(request, operations):
            def exchange(action, seconds, *, payload=None, space=0, boot='-'):
                self.assertTrue(lease.status(request)['ownership_verified'])
                sent.append(action)
                if action in ('reserve', 'run'):
                    self.assertEqual(json.loads((attempts/f'{kind}-sent.json').read_bytes())['attempt'],attempt.name)
                if action == 'finalize':
                    self.assertEqual(json.loads((attempt/'positive-device-closeout.json').read_bytes())['boot_id'],BOOT)
                if action == 'close' and kind == 'measure':
                    self.assertEqual(json.loads((attempt/'repair-sent.json').read_bytes())['boot_id'], BOOT)
                if replies and action in replies:
                    status, raw = replies[action]
                    operations.append({'action':action,'status':status,'refusals':[]})
                    return status, raw
                if action == 'reserve' and 'lost_before_reserve' in faults or action == 'admit' and 'no_admit' in faults:
                    operations.append({'action':action,'status':255,'refusals':[]})
                    return 255,b''
                if action == 'reserve' and 'native_changed_after_admit' in faults:
                    (root/'native_busy').touch()
                if action == 'admit' and 'concurrent_recover' in faults:
                    self.assertFalse((attempts/f'{kind}-sent.json').exists())
                    with self.assertRaisesRegex(ValueError, 'attempt_operator_running'):
                        operate(True)
                    self.assertTrue(lease.status(request)['ownership_verified'])
                if action == 'close' and 'close_not_observed_by_device' in faults:
                    operations.append({'action':action,'status':'timeout','refusals':[]})
                    return 'timeout', b''
                p=d.call(action,boot)
                status, data = p.returncode, p.stdout
                if action+'-lost' in faults:
                    status, data = 255,b''
                if action == 'readback' and 'first_readback_lost' in faults and sent.count('readback') == 1:
                    status, data = 'timeout',b''
                if action == 'readback' and 'wrong_readback_boot' in faults:
                    data = data.replace(BOOT.encode(), b'99999999-2222-3333-4444-555555555555')
                refusals = [line[len(b'AB_REFUSED '):].decode() for line in p.stderr.splitlines() if line.startswith(b'AB_REFUSED ')]
                if action == 'absent' and 'absence_refused' in faults:
                    status,data = 1,b''
                operations.append(dict(action=action,status=status,refusals=refusals))
                if action == 'run' and 'malformed' in faults:
                    data=b'bad sample\n'+data
                if action == 'run' and 'typed_run_refusal' in faults:
                    self.assertEqual(status,0)
                    status = 1
                    operations[-1].update(status=1, refusals=['library_projection_or_worker_exit'])
                    data = (b'STOCK_FAILURE control-01 unchanged_log_parser_rejected\n'
                            b'STOCK_FAILURE_COUNTS 1 0 1 1 0\nSTOCK_FAILURE_REASON library_send_missing\n'
                            +f'AB_RESTORED measure {LABEL}\n'.encode())
                return status,data
            return exchange

        def decode(raw, mode):
            self.assertFalse(base['LEASE'].exists(), 'Do not decode while owning the lease')
            decode_count.append(mode)
            if raw.startswith(b'bad sample'):
                raise ValueError('malformed_returned_evidence')
            if mode == 'locator':
                return [], {('stock','locator'): b'synthetic never a live locator'}
            return [{'name':'control-01'}], {('library','control-01'):b'fixture frame', ('trace','control-01'):b'fixture trace'}

        def operate(recover=False):
            return RUN['operate'](attempts, attempt, kind, d.script.read_bytes(), d.payload,500,base,lease,factory,decode,
                                  recover=recover,not_after=datetime.now(timezone.utc)+timedelta(seconds=timeout))
        return d, attempt, base['LEASE'], sent, decode_count, operate, faults

    def test_clean_visit_hands_back_before_decoding(self):
        for kind in ('locator','measure'):
            with self.subTest(kind=kind):
                d, a, lock, sent, decoded, operate, _=self.fixture(kind)
                report=operate()
                self.assertTrue(report['complete'],report)
                self.assertFalse(lock.exists())
                self.assertEqual(sent,['admit','reserve','run','finalize','absent'])
                self.assertEqual(decoded,[kind])

    def test_run_reply_lost_recovers_original_without_another_click(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('measure',('run-lost',))
        r=operate()
        self.assertFalse(r['complete'])
        self.assertFalse(r['recovery_required'],r)
        self.assertEqual(sent,['admit','reserve','run','readback','finalize','absent'])
        self.assertEqual(decoded,[])
        self.assertFalse(lock.exists())
        self.assertEqual((d.root/'clicks').read_text(),'one click\n')

    def test_exact_reserve_refusal_before_mutation_releases_without_polling_native(self):
        for kind in ('locator', 'measure'):
            with self.subTest(kind=kind):
                d,a,lock,sent,decoded,operate,_ = self.fixture(kind, ('native_changed_after_admit',))
                r = operate()
                self.assertEqual(sent, ['admit','reserve'])
                self.assertEqual(r['operations'][1]['admission_reason'], 'native_foreground_unverified')
                self.assertEqual(r['closeout'], 'untouched_reservation_refused')
                self.assertFalse(r['recovery_required'], r)
                self.assertFalse(lock.exists() or d.stage.exists() or d.state.exists())
                self.assertTrue((a/'reserve-refused.json').is_file())
                self.assertFalse((a/'run-intent.json').exists())
                self.assertEqual(decoded, [])

    def test_signal_after_parsed_reserve_refusal_still_releases_untouched(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('native_changed_after_admit',))
        function_globals = RUN['operate_locked'].__globals__
        require = function_globals['require']
        def interrupt_after_parse(ok, reason):
            if reason == 'reservation_not_acknowledged' and not ok:
                raise KeyboardInterrupt
            return require(ok,reason)
        with patch.dict(function_globals,require=interrupt_after_parse):
            r = operate()
        self.assertEqual(sent,['admit','reserve'])
        self.assertEqual(r['closeout'],'untouched_reservation_refused')
        self.assertFalse(r['recovery_required'] or lock.exists())
        self.assertTrue((a/'reserve-refused.json').is_file() and (a/'released.json').is_file())
        self.assertFalse((a/'run-intent.json').exists())
        self.assertEqual(decoded, [])

    def test_invocation_can_recover_durably_refused_reserve_but_not_contradictory_evidence(self):
        for contradiction in (False, True):
            with self.subTest(contradiction=contradiction):
                d,a,lock,sent,decoded,operate,faults = self.fixture('measure', ('native_changed_after_admit','release_interrupted'))
                self.assertTrue(operate()['recovery_required'])
                faults.remove('release_interrupted')
                if contradiction:
                    (a/'run-intent.json').touch()
                r = operate(True)
                self.assertEqual(sent, ['admit','reserve'])
                self.assertEqual(r['recovery_required'], contradiction, r)
                self.assertEqual(lock.exists(), contradiction)
                self.assertEqual((a/'released.json').exists(), not contradiction)
                self.assertEqual(decoded, [])

    def test_bound_reserve_refusal_with_uncertain_transport_never_becomes_no_mutation_proof(self):
        frame = f'AB_ADMISSION_REFUSED reserve locator {LABEL} {BOOT} profile_unverified\n'.encode()
        for status, reply in ((255,frame), ('timeout',frame), (1,frame[:-1]), (1,frame+frame),
                              (1,frame.replace(BOOT.encode(),(BOOT[:-1]+'9').encode()))):
            with self.subTest(status=status, reply=reply):
                d,a,lock,sent,decoded,operate,_ = self.fixture('locator', replies={'reserve':(status,reply)})
                r = operate()
                self.assertEqual(sent, ['admit','reserve','close','close'])
                self.assertTrue(r['recovery_required'], r)
                self.assertFalse((a/'reserve-refused.json').exists() or (a/'released.json').exists())
                self.assertTrue(lock.exists())
                self.assertEqual(decoded, [])

    def test_reserve_reply_lost_does_not_run(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('locator',('reserve-lost',))
        r=operate()
        self.assertEqual(sent,['admit','reserve','close','finalize','absent'])
        self.assertFalse(r['complete'])
        self.assertFalse(r['recovery_required'],r)
        self.assertFalse(lock.exists())

    def test_absence_alone_does_not_prove_reserve_was_never_dispatched(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('locator',('lost_before_reserve',))
        r=operate()
        self.assertEqual(sent,['admit','reserve','close','close'])
        self.assertTrue(r['recovery_required'])
        self.assertTrue(lock.exists())
        self.assertEqual(decoded,[])
        self.assertFalse((a/'released.json').exists())

    def test_lost_finalize_ack_with_positive_receipt_can_return(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('locator',('finalize-lost',))
        r=operate()
        self.assertTrue(r['complete'],r)
        self.assertTrue((a/'positive-device-closeout.json').is_file())
        self.assertFalse(lock.exists())
        self.assertEqual(sent[-2:],['finalize','absent'])

    def test_failed_absence_keeps_lease_then_reuses_exact_attempt(self):
        d,a,lock,sent,decoded,operate,faults=self.fixture('measure',('absence_refused',))
        r=operate()
        self.assertTrue(r['recovery_required'])
        self.assertTrue(lock.exists())
        self.assertFalse((a/'released.json').exists())
        faults.clear()
        r=operate(True)
        self.assertTrue(r['complete'],r)
        self.assertEqual(sent[-2:],['finalize','absent'])
        self.assertEqual(decoded,[])
        self.assertEqual((d.root/'clicks').read_text(),'one click\n')

    def test_unacknowledged_queued_click_is_not_retried_or_cleared(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('measure',('queued','run-lost'))
        r=operate()
        self.assertTrue(r['recovery_required'])
        self.assertEqual(sent,['admit','reserve','run','readback','close','readback'])
        self.assertFalse((a/'positive-device-closeout.json').exists())
        self.assertTrue(lock.exists())
        self.assertEqual((d.root/'clicks').read_text(),'one click\n')

    def test_read_only_admission_and_window_errors_can_return_untouched(self):
        for kind in ('locator', 'measure'):
            for faults, timeout in ((('no_admit',),800),((),30)):
                d,a,lock,sent,decoded,operate,_=self.fixture(kind,faults,timeout=timeout)
                r=operate()
                self.assertEqual(sent,['admit'])
                self.assertFalse(lock.exists())
                self.assertFalse(d.state.exists() or d.stage.exists())
                self.assertFalse((a.parent/f'{kind}-sent.json').exists())
                self.assertEqual(r['closeout'],'untouched_read_only_admission')
                self.assertFalse(r['complete'])

    def test_ready_dispatch_fails_busy_without_waiting_or_modifying_foreign_lease(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('foreign_lease_busy',))
        foreign_before = {p.name:p.read_bytes() for p in lock.iterdir()}
        r = operate()
        self.assertIs(r['equipment_busy'],True)
        self.assertFalse(r['recovery_required'] or r['complete'])
        self.assertEqual(sent, [])
        self.assertEqual(decoded, [])
        self.assertFalse((a/'owner.token').exists() or (a.parent/'measure-sent.json').exists())
        self.assertEqual({p.name:p.read_bytes() for p in lock.iterdir()},foreign_before)

    def test_stale_ready_dispatch_rechecks_one_use_intent_before_opening_ssh(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('locator', ('other_plan_finishes_before_acquire',))
        r = operate()
        self.assertEqual(r['error'],'one_visit_plan_already_consumed')
        self.assertFalse(r['recovery_required'] or lock.exists() or d.state.exists() or d.stage.exists())
        self.assertTrue((a/'released.json').is_file())
        self.assertEqual(json.loads((a.parent/'locator-sent.json').read_bytes())['attempt'], 'attempt-'+'a'*16)
        self.assertEqual(sent, [])
        self.assertEqual(decoded, [])

    def test_ready_dispatch_requires_current_approval_and_a_full_execution_budget(self):
        now = datetime(2026, 11, 27, 9, tzinfo=timezone.utc)
        for kind in ('locator', 'measure'):
            seconds = RUN['minimum_visit_seconds'](kind)
            deadline = now + timedelta(seconds=seconds)
            RUN['require_permission_deadline'](deadline, kind, now)
            with self.assertRaisesRegex(ValueError, 'approval_expired_or_too_short'):
                RUN['require_permission_deadline'](deadline, kind, now + timedelta(microseconds=1))
            with self.assertRaisesRegex(ValueError, 'approval_expired_or_too_short'):
                RUN['require_permission_deadline'](deadline.replace(tzinfo=None), kind, now)

    def test_expired_admission_budget_never_acquires_or_sends_ssh(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('partial_acquire_unverified',), timeout=1)
        r = operate()
        self.assertEqual(r['error'], 'remaining_window_too_short')
        self.assertFalse(r['recovery_required'])
        self.assertFalse(lock.exists() or (a/'owner.token').exists() or d.stage.exists() or d.state.exists())
        self.assertEqual(sent, [])
        self.assertEqual(decoded, [])

    def test_admission_replies_must_have_exact_scope_frame_and_exit_status(self):
        good = f'AB_ADMITTED measure {LABEL} {BOOT}\n'.encode()
        refused = f'AB_ADMISSION_REFUSED admit measure {LABEL} - timeout_selftest_failed\n'.encode()
        for status, frame, state, reason in (
            (1, refused, 'refused', 'timeout_selftest_failed'),
            (0, good[:-1], 'invalid_reply', None),
            (0, good+good, 'invalid_reply', None),
            (0, good+b'not a device frame\n', 'invalid_reply', None),
            (0, good.replace(b'measure',b'locator'), 'invalid_reply', None),
            (0, good.replace(LABEL.encode(),b'1'*16), 'invalid_reply', None),
            (0, good.replace(BOOT.encode(),b'invalid-uuid'), 'invalid_reply', None),
            (0, refused, 'invalid_reply', None),
            (1, good, 'unclassified_refusal', None),
            (1, refused[:-1], 'unclassified_refusal', None),
            (1, refused+refused, 'unclassified_refusal', None),
            (1, refused.replace(b'timeout_selftest_failed',b'unexpected_identifier'), 'unclassified_refusal', None),
            (1, refused.replace(b'admit',b'reserve'), 'unclassified_refusal', None),
            (1, refused.replace(b' - ',b' ' + BOOT.encode() + b' '), 'unclassified_refusal', None),
            (255, good, 'transport_uncertain', None),
            ('timeout', refused, 'transport_uncertain', None),
        ):
            with self.subTest(status=status, frame=frame):
                d,a,lock,sent,decoded,operate,_ = self.fixture('measure', replies={'admit':(status,frame)})
                r = operate()
                self.assertEqual(sent, ['admit'])
                row, = r['operations']
                self.assertEqual((row['admission_state'],row.get('admission_reason')), (state, reason))
                self.assertFalse(r['complete'] or r['recovery_required'] or lock.exists())
                self.assertEqual(r['closeout'],'untouched_read_only_admission')
                self.assertTrue((a/'released.json').is_file())
                self.assertFalse(any(p.exists() for p in (a/'admitted-boot.json',a.parent/'measure-sent.json',d.state,d.stage)))
                self.assertEqual(decoded, [])

    def test_spent_input_still_on_home_never_sends_repair_after_lost_run(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('run-lost',))
        write(d.root/'after-open-view', HOME)
        (d.root/'nav_no_effect').touch()
        r = operate()
        self.assertTrue(r['recovery_required'], r)
        self.assertEqual(sent, ['admit', 'reserve', 'run', 'readback'])
        self.assertEqual(operate(True)['operations'][0]['action'], 'readback')
        self.assertEqual(d.inputs(), ['nav-library'])
        self.assertFalse((a/'positive-device-closeout.json').exists())
        self.assertTrue(lock.exists())
        self.assertEqual(decoded, [])

    def test_same_attempt_cannot_concurrently_recover_in_read_only_admission(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('locator', ('concurrent_recover',))
        r = operate()
        self.assertEqual(sent, ['admit','reserve','run','finalize','absent'])
        self.assertTrue(r['complete'], r)
        self.assertFalse(lock.exists())
        self.assertEqual(list(a.glob('recovery-*.json')), [])
        self.assertEqual(decoded, ['locator'])

    def test_only_the_readback_is_retried_after_its_reply_times_out(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('run-lost','first_readback_lost'))
        write(d.root/'after-open-view', HOME)
        r = operate()
        self.assertEqual(sent, ['admit','reserve','run','readback','readback','finalize','absent'])
        self.assertEqual(r['operations'][3]['status'], 'timeout')
        self.assertEqual(r['operations'][4]['readback_state'], 'closed')
        self.assertFalse(r['recovery_required'], r)
        self.assertFalse(lock.exists())
        self.assertEqual(d.inputs(), ['nav-library','restore-pointer'])
        self.assertEqual(decoded, [])

    def test_host_timeout_before_remote_intent_never_authorizes_another_repair(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('close_not_observed_by_device',))
        write(d.root/'after-open-view', HOME)
        (d.root/'skip_run_close').touch()
        r = operate()
        self.assertTrue(r['recovery_required'], r)
        self.assertFalse((d.state/'input-restore-intent').exists())
        self.assertTrue((a/'repair-sent.json').exists())
        self.assertEqual(sent, ['admit','reserve','run','readback','close','readback'])
        r = operate(True)
        self.assertTrue(r['recovery_required'], r)
        self.assertEqual(sent[-2:], ['readback','readback'])
        self.assertEqual(sent.count('close'), 1)
        self.assertTrue(lock.exists())
        self.assertEqual(d.inputs(), [])
        self.assertEqual(decoded, [])

    def test_recovery_rechecks_journal_after_lost_confirmation_without_input(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('confirm-close-lost',))
        write(d.root/'after-open-view', HOME)
        (d.root/'pointer_effect_no_reply').touch()
        r = operate()
        self.assertEqual(sent, ['admit','reserve','run','readback','confirm-close','readback','finalize','absent'])
        self.assertEqual((r['operations'][3]['readback_state'],r['operations'][5]['readback_state']), ('exact','closed'))
        self.assertFalse(r['recovery_required'], r)
        self.assertEqual(r['sample_count'], 0)
        self.assertFalse(lock.exists())
        self.assertEqual(d.inputs(), ['nav-library', 'restore-pointer'])
        self.assertEqual(decoded, [])

    def test_misbound_readback_receipt_never_confirms_or_releases(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('run-lost', 'wrong_readback_boot'))
        r = operate()
        self.assertEqual(sent, ['admit','reserve','run','readback'])
        self.assertTrue(r['recovery_required'], r)
        self.assertTrue(lock.exists())
        self.assertFalse((a/'positive-device-closeout.json').exists())
        self.assertEqual(decoded, [])

    def test_run_terminal_line_must_be_complete_unique_and_at_end(self):
        terminal = f'AB_RESTORED measure {LABEL}\n'.encode()
        for data in (terminal[:-1], terminal+b'extra\n', terminal+terminal,
                     terminal+b'embedded '+terminal, b'embedded '+terminal, terminal.replace(b'\n',b'\r\n')):
            self.assertFalse(RUN['has_terminal'](data,terminal), data)
        self.assertTrue(RUN['has_terminal'](b'previous row\n'+terminal,terminal))

    def test_closed_typed_run_refusal_exports_only_failure_evidence(self):
        d,a,lock,sent,decoded,operate,_ = self.fixture('measure', ('typed_run_refusal',))
        r = operate()
        self.assertEqual(sent,['admit','reserve','run','finalize','absent'])
        self.assertFalse(r['complete'] or r['recovery_required'] or lock.exists())
        self.assertEqual((r['run_status'],r['sample_count']),(1,0))
        self.assertTrue(r['failure_diagnostic_saved'])
        refusal = json.loads((a/'failure-diagnostic.json').read_bytes())
        self.assertEqual(refusal['failure_reasons'],['library_send_missing'])
        self.assertFalse(refusal['timing_qualified'] or (a/'clock-records.json').exists())
        self.assertEqual(decoded,[])

    def test_bad_timing_is_separate_from_verified_release(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('measure',('malformed',))
        r=operate()
        self.assertFalse(r['complete'])
        self.assertEqual(r['evidence_error'],'malformed_returned_evidence',r)
        self.assertFalse(lock.exists())
        self.assertFalse((a/'clock-records.json').exists())

    def test_partial_acquire_touches_no_device_and_releases_only_verified_owner(self):
        for state in ('owned','unverified'):
            with self.subTest(state=state):
                d,a,lock,sent,decoded,operate,_=self.fixture('locator',('partial_acquire_'+state,))
                r=operate()
                self.assertTrue(r['partial_acquire'],r)
                self.assertFalse(r['complete'])
                self.assertEqual(sent,[])
                self.assertEqual(r['recovery_required'],state=='unverified')
                self.assertEqual(lock.exists(),state=='unverified')

    def test_release_with_invalid_proof_never_exports_an_accepted_result(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('measure',('invalid_release_receipt',))
        r=operate()
        self.assertTrue(r['release_unverified'],r)
        self.assertTrue(r['recovery_required'])
        self.assertFalse((a/'released.json').exists())
        self.assertEqual(decoded,[])

    def test_malformed_mutable_intent_during_recovery_cannot_clear_a_lease(self):
        d,a,lock,sent,decoded,operate,_=self.fixture('measure',('queued','run-lost'))
        self.assertTrue(operate()['recovery_required'])
        (a.parent/'measure-sent.json').write_text('{invalid')
        observe=list(sent)
        r=operate(True)
        self.assertEqual(sent,observe)
        self.assertTrue(lock.exists())
        self.assertTrue(r['recovery_required'])
        self.assertFalse((a/'released.json').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
