#!/usr/bin/env python3
"""Replay each read-only predicate in both generated device scripts."""

import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
FIXTURE = runpy.run_path(str(HERE/'test_stock_generation_recovery.py'))
BOOT, LABEL = (FIXTURE[k] for k in ('BOOT', 'LABEL'))


class AdmissionDevice:
    def __init__(self, root, kind):
        self.device = d = FIXTURE['DeviceModel'](root, kind)
        self.root, self.kind = root, kind
        self.bin = root/'bin'
        self.bin.mkdir()
        for tool in ('flock', 'cmp', 'stat', 'readlink', 'gzip', 'base64', 'tar', 'timeout', 'grep', 'sort', 'awk'):
            path = shutil.which(tool)
            if path is None:
                raise RuntimeError(f'Missing Linux fixture tool: {tool}')
            (self.bin/tool).symlink_to(path)
        (root/'framebuffer').touch()
        (root/'df-value').write_text('500\n')
        (root/'true').symlink_to('/bin/true')
        text = d.script.read_text()
        start = text.index('\nnative() {\n')
        end = text.index('\nno_overlay() {', start)
        # Leave the actual generated native guards intact, including the
        # first-failure pipe, and only fake their hardware service endpoints.
        mocks = r'''
id() { [ ! -e "$model/non_root" ] && printf '0\n' || printf '1000\n'; }
status() {
    if [ -e "$model/statusbar_down" ]; then printf 'fixture stop/waiting\n'; else printf 'fixture start/running\n'; fi
}
fixture_lipc_get_prop() {
    case "$3" in
        state) [ ! -e "$model/sleeping" ] && printf 'active\n' || printf 'suspended\n';;
        activeApp) [ ! -e "$model/native_background" ] && printf 'com.lab126.KPPMainApp\n' || printf 'private-foreground-app\n';;
        *) return 1;;
    esac
}
pidof() {
    case "$1" in
        awesome) [ ! -e "$model/window_unlisted" ] && printf '1009 1010\n';;
        aidoku|weread|bilicomics) [ -e "$model/live_reader" ] && printf '1201\n' || return 1;;
        *) return 1;;
    esac
}
proc_row() {
    [ ! -e "$model/window_unreadable" ] || return 1
    [ ! -e "$model/window_stopped" ] && printf '1 202 R\n' || printf '1 202 T\n'
}
df() {
    IFS= read -r available <"$model/df-value" || return 1
    printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
    printf 'private-fs 1000 500 %s 50%% private-mount\n' "$available"
    [ ! -e "$model/df_failed" ] || { printf 'private df error details\n' >&2; return 1; }
}
'''
        text = text[:start]+'\n'+mocks+text[end:]
        text = text.replace('$(lipc-get-prop ', '$(fixture_lipc_get_prop ')
        text = FIXTURE['replace'](text, 'absent /var/local/aidoku-emulator-id && [ -c /dev/fb0 ]',
                                  'absent "$model/emulator_marker" && [ -f "$model/framebuffer" ]')
        text = FIXTURE['replace'](text, 'absent "/tmp/$reader-launcher.lock"', 'absent "$model/$reader-launcher.lock"')
        if kind == 'measure':
            self.assert_probe_count(text)
            text = text.replace('/bin/true', '"$model/true"')
        d.script.write_text(text)

    @staticmethod
    def assert_probe_count(text):
        if text.count('/bin/true') != 2:
            raise ValueError('Unexpected timeout target in generated fixture')

    def invoke(self, action='admit'):
        return subprocess.run(['/bin/sh', *self.device.argv(action)[1:]],
                              env=dict(os.environ, PATH=str(self.bin)), capture_output=True, timeout=8)

    def stub_timeout(self, command):
        (self.bin/'timeout').unlink()
        path = self.bin/'timeout'
        path.write_text('#!/bin/sh\n'+command+'\n')
        path.chmod(0o700)

    def state(self):
        # Check that we do not create/delete files, write existing data, or
        # touch paths that are already present when a predicate is refused.
        entries = {}
        for p in self.root.rglob('*'):
            s = p.lstat()
            entries[str(p.relative_to(self.root))] = (s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns,
                                                      p.readlink() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
        return entries


class AdmissionTests(unittest.TestCase):
    def fixture(self, kind):
        directory = tempfile.TemporaryDirectory(prefix='stock-admission-device-')
        self.addCleanup(directory.cleanup)
        return AdmissionDevice(Path(directory.name), kind)

    def refuse(self, model, reason, action='admit'):
        before = model.state()
        result = model.invoke(action)
        after = model.state()
        self.assertEqual(set(after), set(before))
        self.assertTrue(all(before[key]==value for key,value in after.items()), 'The read-only gate changed fixture state')
        expected = f'AB_ADMISSION_REFUSED {action} {model.kind} {LABEL} '+('-' if action == 'admit' else BOOT)+' '+reason+'\n'
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, expected.encode(), b''))
        self.assertEqual(FIXTURE['RUN']['admission_observation'](action, model.kind, LABEL,
                         '-' if action == 'admit' else BOOT, result.returncode, result.stdout), ('refused',reason))
        self.assertFalse((model.root/'clicks').exists() or model.device.inputs())

    def test_common_admission_checks_are_distinct_and_read_only(self):
        for kind in ('locator', 'measure'):
            for fault, reason in (
                ('non_root','privilege_unverified'), ('invalid_path','paths_unverified'),
                ('existing_stage','attempt_paths_not_empty'), ('existing_state','attempt_paths_not_empty'),
                ('foreign_mount','mount_state_unverified'), ('emulator_marker','hardware_unverified'),
                ('live_reader','reader_state_unverified'), ('aidoku-launcher.lock','reader_state_unverified'),
                ('sleeping','power_not_active_or_unreadable'), ('native_background','native_foreground_unverified'),
                ('statusbar_down','statusbar_unverified'), ('window_unlisted','window_manager_unverified'),
                ('window_unreadable','window_manager_unverified'), ('window_stopped','window_manager_unverified'),
                ('wrong_profile','profile_unverified'), ('missing_usual_tool','missing_tool_gzip'),
            ):
                with self.subTest(kind=kind,fault=fault):
                    m = self.fixture(kind)
                    if fault == 'invalid_path':
                        (m.root/'var').rmdir()
                        (m.root/'var').symlink_to(m.root/'bin')
                    elif fault == 'existing_stage':
                        m.device.stage.mkdir()
                        (m.device.stage/'private-foreign').write_text('not ours\n')
                    elif fault == 'existing_state':
                        m.device.state.mkdir()
                        (m.device.state/'private-foreign').write_text('not ours\n')
                    elif fault == 'wrong_profile':
                        (m.device.home/'data/device-profile').write_text('pw6\n')
                    elif fault == 'missing_usual_tool':
                        (m.bin/'gzip').unlink()
                    else:
                        (m.root/fault).touch()
                    self.refuse(m,reason)

    def test_space_read_error_format_and_threshold_are_not_conflated(self):
        for kind in ('locator','measure'):
            m = self.fixture(kind)
            accepted = m.invoke()
            self.assertEqual(accepted.returncode,0,accepted)
            self.assertEqual(accepted.stdout,f'AB_ADMITTED {kind} {LABEL} {BOOT}\n'.encode())
            (m.root/'df_failed').touch()
            self.refuse(m,'space_read_failed')  # Even a plausible printed row must not hide this failure.
            (m.root/'df_failed').unlink()
            for value, reason in [('NaN','space_unverified'),('-10','space_unverified'),('99999999999999999999999','space_unverified'),('499','space_requirement_not_met')]:
                (m.root/'df-value').write_text(value+'\n')
                self.refuse(m,reason)

    def test_timeout_is_only_required_for_measure_and_failure_is_diagnostic(self):
        for kind in ('locator','measure'):
            m = self.fixture(kind)
            (m.bin/'timeout').unlink()
            if kind == 'locator':
                self.assertEqual(m.invoke().returncode,0)
                continue
            self.refuse(m,'missing_tool_timeout')
            (m.bin/'timeout').symlink_to(shutil.which('timeout'))
            (m.root/'true').unlink()
            self.refuse(m,'timeout_probe_target_missing')
            (m.root/'true').symlink_to('/bin/true')
            m.stub_timeout('printf "private timeout failure\\n" >&2; exit 64')
            self.refuse(m,'timeout_selftest_failed')
            m.stub_timeout('printf "unexpected stdout\\n"; exit 0')
            self.refuse(m,'timeout_selftest_failed')

    def test_read_only_navigation_rejects_failed_queries_with_plausible_stdout(self):
        prepare = FIXTURE['PREPARE']
        original = (prepare['private_path']('classifier')/'navigation.sh').read_bytes()
        generated = prepare['measure_navigation'](original).decode('ascii')
        with tempfile.TemporaryDirectory(prefix='stock-timed-query-') as directory:
            root = Path(directory)
            for name in ('state', 'stage'):
                (root/name).mkdir()
                (root/name/'owner').write_text(LABEL+'\n')
            commands = root/'bin'
            commands.mkdir()
            timeout = commands/'timeout'
            timeout.write_text(r'''#!/bin/sh
[ ! -e /proc/$$/fd/9 ] && [ "$1 $2 $3" = '-s KILL 3' ] || exit 92
case "$*" in
    *'lipc-get-prop -s com.lab126.powerd state') query=power; printf 'active\n';;
    *'lipc-get-prop -s com.lab126.appmgrd activeApp') query=app; printf 'com.lab126.KPPMainApp\n';;
    *'status statusbar') query=bar; printf 'statusbar start/running\n';;
    *) exit 93;;
esac
printf '%s\n' "$query" >>"$FIXTURE/queries"
[ "$query" != "$FAULT" ] || exit 137
''')
            timeout.chmod(0o700)
            script = root/'navigation.sh'
            generated = FIXTURE['replace'](generated, 'stage=/mnt/us/.aidoku-native-$label', 'stage=$FIXTURE/stage')
            generated = FIXTURE['replace'](generated, 'state=/var/tmp/aidoku-native-$label', 'state=$FIXTURE/state')
            # None of the three truncated queries may reach the WM guard or OCR.
            generated = FIXTURE['replace'](generated, '    pids=$(pidof awesome)',
                                          '    printf "reached\\n" >"$FIXTURE/unexpected"\n    pids=$(pidof awesome)')
            script.write_text(generated)
            for failure, expected in (('power', ('power',)), ('app', ('power', 'app')), ('bar', ('power', 'app', 'bar'))):
                with self.subTest(failure=failure):
                    (root/'queries').unlink(missing_ok=True)
                    env = dict(os.environ, PATH=f'{commands}:{os.environ["PATH"]}', FIXTURE=directory, FAULT=failure)
                    result = subprocess.run(['/bin/sh', '-c', 'exec 9<"$FIXTURE/state"; exec /bin/sh "$@"',
                                             'fixture', str(script), 'inspect', LABEL], env=env,
                                            capture_output=True, timeout=8)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b'', b'refused stock_tabs\n'))
                    self.assertEqual(tuple((root/'queries').read_text().splitlines()), expected)
                    self.assertFalse((root/'unexpected').exists())

    def test_boot_reads_require_one_full_typed_line(self):
        for kind in ('locator','measure'):
            m = self.fixture(kind)
            for value, reason in [(None,'boot_unreadable'),(BOOT,'boot_unreadable'),('bad\n','boot_invalid'),
                                  (BOOT+'\nprivate-boot-data\n','boot_invalid')]:
                if value is None:
                    m.device.boot.unlink()
                else:
                    m.device.boot.write_text(value)
                self.refuse(m,reason)

    def test_unexpected_predicate_exit_cannot_emit_bound_no_mutation_proof(self):
        m = self.fixture('measure')
        source = FIXTURE['replace'](m.device.script.read_text(), '    native admission 8>&1 >/dev/null || exit 1',
                                    '    printf "native_foreground_unverified\\n"; exit 2')
        m.device.script.write_text(source)
        before = m.state()
        result = m.invoke('reserve')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1,b'',b'AB_REFUSED admission_internal_error\n'))
        self.assertEqual(FIXTURE['RUN']['admission_observation']('reserve','measure',LABEL,BOOT,result.returncode,result.stdout),
                         ('unclassified_refusal', None))
        self.assertEqual(m.state(),before)

    def test_reserve_rechecks_after_admit_without_starting_its_journal(self):
        for kind in ('locator','measure'):
            m = self.fixture(kind)
            self.assertEqual(m.invoke().returncode,0)
            (m.root/'native_background').touch()
            self.refuse(m,'native_foreground_unverified','reserve')
            self.assertFalse(m.device.state.exists() or m.device.stage.exists())
            m.device.boot.write_text(BOOT[:-1]+'9\n')
            self.refuse(m,'reservation_boot_unverified','reserve')
            self.assertFalse(m.device.state.exists() or m.device.stage.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
