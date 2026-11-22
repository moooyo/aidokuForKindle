import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

import test_selector


HERE = Path(__file__).resolve().parent
LABEL = test_selector.LABEL
CASE = 'control-01'

GUARD = r'''#!/bin/sh
set -eu
case "$1" in
capture)
    mkdir "$MOCK_STATE/guard"
    (set -C; printf '%s\n' "$2" >"$MOCK_STATE/guard/ready")
    printf 'capture\n' >>"$MOCK_ROOT/guard-events"
    printf 'guard_captured\n';;
verify|restore)
    test "$(cat "$MOCK_STATE/guard/ready")" = "$2"
    test ! -f "$MOCK_ROOT/guard-fail"
    printf '%s\n' "$1" >>"$MOCK_ROOT/guard-events"
    printf 'guard_verified\n';;
*) exit 1;;
esac
'''

NAVIGATION = r'''#!/bin/sh
set -eu
case "$1" in
library)
    test ! -f "$MOCK_ROOT/navigation-fail"
    test "$(lipc-get-prop -s com.lab126.powerd state)" = active
    (set -C; cat "$MOCK_ROOT/pointer" >"$MOCK_STATE/input-original")
    printf 'library 200 300\n' >"$MOCK_ROOT/pointer"
    printf 'library\n' >>"$MOCK_ROOT/input-events";;
restore)
    test "$(lipc-get-prop -s com.lab126.powerd state)" = active
    if [ -f "$MOCK_STATE/input-original" ]; then
        cat "$MOCK_STATE/input-original" >"$MOCK_ROOT/pointer"
        rm "$MOCK_STATE/input-original"
    fi
    printf 'restore\n' >>"$MOCK_ROOT/input-events";;
*) exit 1;;
esac
'''

DISPATCH = r'''#!/bin/sh
set -eu
out="$MOCK_STATE/out/$3"
case "$1" in
target)
    test ! -f "$MOCK_ROOT/unknown-target"
    (set -C; printf '%s\n' "$3" >"$out/unique-tile") ;;
tap)
    test -f "$out/unique-tile"
    (set -C; printf 'stock-tap\n' >"$out/dispatched")
    down_before=$(clock-usec)
    down_after=$(clock-usec)
    sleep 0.06
    up_before=$(clock-usec)
    up_after=$(clock-usec)
    (set -C; printf '%s %s %s %s\n' "$down_before" "$down_after" "$up_before" "$up_after" >"$out/pointer-brackets")
    sh "$MOCK_HOME/launch.sh" --foreground 7>&- </dev/null >/dev/null 2>>"$MOCK_HOME/data/logs/launcher.log" &
    printf '%s %s\n' "$down_before" "$down_after"
    test ! -f "$MOCK_ROOT/dispatch-fail" ;;
*) exit 1;;
esac
'''

VISIT = r'''#!/bin/sh
set -eu
out="$MOCK_STATE/visit-$3"
live="$MOCK_STATE/visit-live"
birth() { awk '{print $22}' "/proc/$1/stat"; }
stop_owned() {
    if [ -f "$MOCK_ROOT/gui" ]; then
        gui=$(cat "$MOCK_ROOT/gui")
        kill -TERM "$gui" 2>/dev/null || :
    fi
    tries=200
    until grep -Fq 'aidoku: Launcher worker exited with status 0.' "$MOCK_HOME/data/logs/launcher.log"; do
        tries=$((tries - 1)); [ "$tries" -gt 0 ] || return 1; sleep 0.01
    done
}
case "$1" in
prepare)
    mkdir "$out" "$live"
    (set -C; printf '%s %s\n' "$2" "$3" >"$out/owner")
    (set -C; printf '%s %s\n' "$2" "$3" >"$live/case")
    cp /proc/sys/kernel/random/boot_id "$out/boot"
    stat -c '%d:%i %s' "$MOCK_HOME/data/logs/launcher.log" >"$out/log-mark"
    printf 'prepare\n' >>"$MOCK_ROOT/visit-events";;
arm)
    test ! -f "$MOCK_ROOT/arm-fail"
    (set -C; printf '%s\n' "$4" >"$out/request")
    printf 'arm\n' >>"$MOCK_ROOT/visit-events";;
bind)
    tries=200
    until [ -s "$MOCK_ROOT/gui" ] && [ -s "$MOCK_ROOT/runner" ] && [ -s "$MOCK_ROOT/worker" ]; do
        tries=$((tries - 1)); [ "$tries" -gt 0 ] || exit 1; sleep 0.01
    done
    gui=$(cat "$MOCK_ROOT/gui"); runner=$(cat "$MOCK_ROOT/runner"); worker=$(cat "$MOCK_ROOT/worker")
    (set -C; printf '%s %s %s %s %s %s %s\n' "$gui" "$(birth "$gui")" "$runner" "$(birth "$runner")" \
        "$worker" "$(birth "$worker")" 0123456789abcdef0123456789abcdef >"$out/bound")
    (set -C; printf 'observed\n' >"$out/ready")
    printf 'bind\n' >>"$MOCK_ROOT/visit-events";;
check)
    test -s "$out/bound"
    printf 'check\n' >>"$MOCK_ROOT/visit-events";;
exit)
    test ! -f "$MOCK_ROOT/exit-fail"
    stop_owned
    (set -C; printf 'observed\n' >"$out/ack")
    (set -C; printf 'qualified\n' >"$out/complete")
    rm "$live/case" && rmdir "$live"
    printf 'exit\n' >>"$MOCK_ROOT/visit-events";;
recover)
    if [ -d "$live" ]; then
        if [ -f "$MOCK_STATE/out/$3/dispatched" ]; then stop_owned; fi
        rm "$live/case" && rmdir "$live"
    fi
    printf 'recover\n' >>"$MOCK_ROOT/visit-events";;
*) exit 1;;
esac
'''

LAUNCHER = r'''#!/bin/sh
set -eu
[ "$1" = --foreground ]
printf '%s\n' "$$" >"$MOCK_ROOT/worker"
printf 'aidoku: Launcher worker started (pid=%s).\n' "$$" >&2
sh "$MOCK_ROOT/runner.sh" "$MOCK_HOME/app/aidoku" --kindle --device scribe --data-dir "$MOCK_HOME/data" &
runner=$!
printf '%s\n' "$runner" >"$MOCK_ROOT/runner"
wait "$runner"
printf 'aidoku: Launcher worker exited with status 0.\n' >&2
'''

RUNNER = r'''#!/bin/sh
set -eu
"$@" &
gui=$!
printf '%s\n' "$gui" >"$MOCK_ROOT/gui"
wait "$gui"
'''

APP = r'''#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
static volatile sig_atomic_t stopped;
static void stop(int unused) { (void)unused; stopped = 1; }
int main(void) {
    struct timespec time;
    signal(SIGTERM, stop);
    if (!getenv("MOCK_NO_SUBMISSION")) {
        if (clock_gettime(CLOCK_BOOTTIME, &time)) return 1;
        fprintf(stderr, "aidoku: startup_library_submitted boottime_us=%lld sequence=1 marker=1 layout=large library=nonempty grid=nonempty\n",
                (long long)time.tv_sec * 1000000LL + time.tv_nsec / 1000);
        fflush(stderr);
    }
    while (!stopped) usleep(10000);
    return 0;
}
'''


class DeviceIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        outer = os.environ.get('AIDOKU_TEST_OUTER_MNT')
        if not outer or os.readlink('/proc/self/ns/mnt') == outer:
            raise unittest.SkipTest('Require a fresh private mount namespace on test-env')

    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='jpeg-device-integration-'))
        self.root = Path(self.temporary)
        self.fixture = test_selector.Fixture(self.root)
        self.stage, self.state = self.fixture.stage, self.fixture.state
        self.home, self.media = self.fixture.home, self.fixture.store
        self.private, self.bin = self.root / 'private', self.fixture.bin
        self.pointer = self.root / 'pointer'
        self.pointer.write_text('home 99 104\n', encoding='ascii')
        self.home.joinpath('data/logs').mkdir(parents=True)
        self.log = self.home / 'data/logs/launcher.log'
        self.log.write_text('previous benign log\n', encoding='ascii')
        launcher = self.home / 'launch.sh'
        launcher.write_text(LAUNCHER, encoding='ascii')
        launcher.chmod(0o752)
        runner = self.root / 'runner.sh'
        runner.write_text(RUNNER, encoding='ascii')
        runner.chmod(0o700)
        source = self.root / 'app.c'
        source.write_text(APP, encoding='ascii')
        self.program = self.root / 'selected-app'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(self.program), str(source)], check=True)
        self.program.chmod(0o751)
        self.fixture.original = [self.fixture.identity(self.home / name) for name in test_selector.NAMES]
        self.selector = self.fixture.script
        self.rename = (self.stage / 'rename-new').read_text(encoding='ascii')
        shutil.rmtree(self.stage)
        shutil.rmtree(self.state)
        submission = (HERE / 'submission.sh').read_text(encoding='ascii')
        self.submission = submission.replace('media=/mnt/us', f'media={self.media}').replace(
            'private=/var/tmp', f'private={self.private}')
        self.shell = (HERE / 'device.sh').read_text(encoding='ascii').replace(
            'media=/mnt/us', f'media={self.media}').replace('private=/var/tmp', f'private={self.private}').replace(
            '"/tmp/$reader-launcher.lock"', f'"{self.fixture.tmp}/$reader-launcher.lock"').replace(
            '"/tmp/.aidoku-selector-lock-$label"', f'"{self.fixture.tmp}/.aidoku-selector-lock-$label"')
        self.modules = {
            'guard/guard.sh': GUARD.encode(),
            'visit/visit.sh': VISIT.encode(),
            'input/navigation.sh': NAVIGATION.encode(),
            'input/dispatch.sh': DISPATCH.encode(),
            'input/artwork.guard': b'fixture-artwork-template\n',
            'input/tile-input': b'fixture-tile-tool\n',
            'selector/selector.sh': self.selector.encode(),
            'submission/submission.sh': self.submission.encode(),
            'programs/selected-app': self.program.read_bytes(),
            'rename-new': self.rename.encode(),
            'control/program-modes': b'751\n752\n753\n',
            'control/launch.sh': LAUNCHER.encode(),
            'control/app/aidoku': (self.home / 'app/aidoku').read_bytes(),
            'control/app/aidoku-startup-portrait': (self.home / 'app/aidoku-startup-portrait').read_bytes(),
        }
        self.shim('lipc-get-prop', r'''case "$*" in
    *"powerd state") if [ -f "$MOCK_ROOT/asleep" ]; then echo screenSaver; else echo active; fi;;
    *activeApp) echo com.lab126.KPPMainApp;;
    *flIntensity) cat "$MOCK_ROOT/light-flIntensity";;
    *currentAmberLevel) cat "$MOCK_ROOT/light-currentAmberLevel";;
    *) exit 1;;
esac''')
        self.shim('lipc-set-prop', r'''case "$*" in
    *touchScreenSaverTimeout*) printf 'touch\n' >>"$MOCK_ROOT/native-events";;
    *) printf '%s\n' "$4" >"$MOCK_ROOT/light-$3";;
esac''')
        self.shim('usleep', '/bin/sleep 0.001')
        self.shim('clock-usec', "exec python3 -c 'import time; print(time.clock_gettime_ns(time.CLOCK_BOOTTIME)//1000)'")
        self.shim('cmp', r'''if [ "$#" = 3 ] && [ "$1" = -s ] && [ "$3" = /proc/sys/kernel/random/boot_id ]; then
    cat "$3" | /usr/bin/cmp -s "$2" -
else
    exec /usr/bin/cmp "$@"
fi''')
        for name, value in (('light-flIntensity', '5\n'), ('light-currentAmberLevel', '0\n')):
            (self.root / name).write_text(value, encoding='ascii')
        self.environment = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
            MOCK_ROOT=str(self.root), MOCK_STATE=str(self.state), MOCK_HOME=str(self.home),
            BREAK_FILE=str(self.root / 'count'), BREAK_SIGNAL='KILL', BREAK_AFTER='',
            RENAME_IMPL=str(self.root / 'rename-system'), RENAME_DEVICES=str(self.root / 'devices'))

    def tearDown(self):
        if test_selector.mount_rows(self.home / 'app/aidoku'):
            subprocess.run(['/bin/umount', str(self.home / 'app/aidoku')], check=False)
        gui = self.root / 'gui'
        if gui.is_file():
            try:
                os.kill(int(gui.read_text()), 15)
            except ProcessLookupError:
                pass

    def shim(self, name, commands):
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + commands + '\n', encoding='ascii')
        path.chmod(0o700)

    def payload(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in self.modules.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o755
                archive.addfile(member, io.BytesIO(data))
        return buffer.getvalue()

    def command(self, operation, *arguments, data=b'', success=True):
        result = subprocess.run(['/bin/sh', '-c', self.shell, 'device', operation, LABEL, *arguments],
                                input=data, capture_output=True, env=self.environment, timeout=40)
        self.assertEqual(result.returncode == 0, success, (operation, result.stdout, result.stderr))
        return result

    def prepare(self, case=CASE, *, success=True):
        return self.command('prepare', case, data=self.payload(), success=success)

    def finish(self):
        self.assertEqual(self.command('close').stdout, b'jpeg_closed\n')
        self.assertEqual(self.command('absent').stdout, b'jpeg_absent\n')
        self.fixture.original_restored()
        self.assertEqual(self.pointer.read_text(encoding='ascii'), 'home 99 104\n')

    def test_real_selector_and_submission_complete_a_single_qualified_visit(self):
        self.assertEqual(self.command('admit').stdout, b'jpeg_admitted\n')
        self.assertEqual(self.prepare().stdout, b'jpeg_prepared\n')
        self.assertEqual(self.command('visit', CASE).stdout, b'jpeg_completed\n')
        archive = self.command('collect', CASE).stdout
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as stream:
            records = {item.name: stream.extractfile(item).read() for item in stream}
        prefix = 'out/' + CASE + '/'
        self.assertEqual(set(records), {prefix + name for name in ('origin', 'anchor', 'pointer-brackets',
                                                                   'qualified', 'submission')})
        self.assertEqual(records[prefix + 'anchor'], records[prefix + 'pointer-brackets'])
        self.assertRegex(records[prefix + 'submission'], rb'^SUBMISSION [0-9]+ [0-9]+ 1 1 large nonempty nonempty\n$')
        self.finish()

    def test_screensaver_refuses_before_stage_or_navigation(self):
        (self.root / 'asleep').touch()
        self.command('admit', success=False)
        self.prepare(success=False)
        self.assertFalse(self.stage.exists() or self.state.exists())
        self.assertFalse((self.root / 'input-events').exists())
        self.assertFalse((self.root / 'native-events').exists())

    def test_case_mismatch_never_dispatches_and_original_is_restored(self):
        self.prepare()
        self.command('visit', 'candidate-01', success=False)
        self.assertFalse((self.root / 'worker').exists())
        self.finish()

    def test_missing_submission_recovers_owned_gui_and_restores_selector(self):
        self.prepare()
        self.environment['MOCK_NO_SUBMISSION'] = '1'
        result = self.command('visit', CASE, success=False)
        self.assertIn(b'refused submission_timeout\n', result.stderr)
        self.assertTrue((self.state / 'out' / CASE / 'pointer-brackets').is_file())
        self.assertTrue((self.root / 'gui').is_file())
        self.assertTrue((self.state / 'current').is_file())
        self.finish()
        self.assertIn('recover\n', (self.root / 'visit-events').read_text(encoding='ascii'))

    def test_lost_selector_process_after_bind_is_recovered_by_close(self):
        self.environment['BREAK_AFTER'] = '1'
        self.prepare(success=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        self.assertTrue(test_selector.mount_rows(self.home / 'app/aidoku'))
        self.assertTrue(self.fixture.lock.is_dir())
        self.finish()

    def test_candidate_case_uses_the_same_stock_and_submission_contract(self):
        case = 'candidate-06'
        self.prepare(case)
        self.assertEqual(self.command('visit', case).stdout, b'jpeg_completed\n')
        self.finish()

    def test_guard_failure_prevents_close_and_keeps_owned_state(self):
        self.prepare()
        (self.root / 'guard-fail').touch()
        self.command('close', success=False)
        self.assertTrue(self.stage.is_dir() and self.state.is_dir())
        (self.root / 'guard-fail').unlink()
        self.finish()


if __name__ == '__main__':
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise SystemExit('Require the test-env private Linux worker')
    if 'AIDOKU_TEST_OUTER_MNT' not in os.environ:
        outer = os.readlink('/proc/self/ns/mnt')
        os.execvpe('unshare', ['unshare', '--mount', '--propagation', 'private', '--fork', '--',
                              sys.executable, '-B', str(Path(__file__).resolve()), *sys.argv[1:]],
                   dict(os.environ, AIDOKU_TEST_OUTER_MNT=outer))
    if os.readlink('/proc/self/ns/mnt') == os.environ['AIDOKU_TEST_OUTER_MNT']:
        raise SystemExit('The fixture must not run in its parent mount namespace')
    unittest.main(verbosity=2)
