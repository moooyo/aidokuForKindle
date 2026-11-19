#!/usr/bin/env python3
"""Run the frozen launcher lifecycle scenarios with fixture-owned processes only."""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import traceback


FROZEN_TEST = Path(os.environ.get('AIDOKU_R3_FROZEN_LAUNCHER_TEST', '/root/aidoku-all-build-20261119/sources/final-v2/packaging/tests/launcher_test.py'))
FROZEN_TEST_SHA = '78323cfbe8fd397d8506affa5ff0ae0844bce63a4a2ff9eac3d660f4a6c1e47f'
HOST_TOOLS = 'awk basename cat cut dd dirname env grep head mkdir mkfifo mv nohup od perl printf python3 readlink rm rmdir sed setsid sh sha256sum sleep sort stat tail touch tr uniq wc'.split()
ALLOWED_ABSOLUTE = {
    '/bin/sh', '/dev/fb0', '/dev/null', '/mnt/us/aidoku', '/mnt/us/documents/Aidoku.sh',
    '/proc/uptime', '/proc/self/stat', '/proc/sys/kernel/random/uuid',
    '/tmp/aidoku-launcher.lock', '/var/local/kindle-catalog-entry', '/var/local/system/userpasswdenabled',
}
NATIVE_PORTRAIT = r'''
import json
from pathlib import Path
import os
import subprocess
import sys
import time

launcher, sequence, profile, *capability = sys.argv[1:]
assert launcher == str(os.getppid()) and sequence.isdecimal()
assert profile == 'pw6' and capability in ([], ['--capture-sleep-capability'])
pid = os.getpid()
os.setsid()
lock = Path(os.environ['AIDOKU_LAUNCHER_DIR'])
nonce = os.environ['AIDOKU_LAUNCH_NONCE']
assert (lock / 'pid').read_text() == launcher + '\n'
assert (lock / 'nonce').read_text() == nonce + '\n'
permission = f'{pid}:{sequence}:{nonce}\n'
deadline = time.monotonic() + 2
while time.monotonic() < deadline:
    try:
        if (lock / 'native-read-go').read_text() == permission:
            break
    except FileNotFoundError:
        pass
    time.sleep(0.005)
else:
    raise SystemExit(1)
with (Path(os.environ['AIDOKU_TEST_ROOT']) / 'native-helper.jsonl').open('a') as trace:
    trace.write(json.dumps({'pid': pid, 'owner': int(launcher), 'sequence': int(sequence), 'profile': profile, 'capability': bool(capability)}) + '\n')
result = subprocess.run([str(Path(sys.argv[0]).with_name('aidoku')), '--kindle', '--device', profile,
                         '--capture-native-portrait', *capability], capture_output=True, timeout=1)
assert (lock / 'native-read-go').read_text() == permission
sys.stdout.buffer.write(result.stdout)
sys.stdout.buffer.flush()
pending = lock / 'native-read-done.pending'
pending.write_text(f'{sequence}:{nonce}:{int(result.returncode != 0)}\n')
pending.replace(lock / 'native-read-done')
while time.monotonic() < deadline:
    time.sleep(0.01)
'''


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(pid):
    descendants = set()
    try:
        children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
    except FileNotFoundError:
        return descendants
    for value in children:
        child = int(value)
        descendants.add(child)
        descendants.update(tree(child))
    return descendants


def reap_owned(keep):
    for child in tree(os.getpid()) - keep:
        try:
            os.waitpid(child, os.WNOHANG)
        except ChildProcessError:
            pass


def stop_owned():
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        children = tree(os.getpid())
        if not children:
            return
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        reap_owned(set())
        time.sleep(0.025)
    require(not tree(os.getpid()), 'Could not clean up the fixture-owned descendants')


def setup(fixture, mode):
    fixture.setUp()
    module_env = {key: value for key, value in fixture.environment.items()
                  if key in {'AIDOKU_HOME', 'AIDOKU_LOCK_DIR', 'AIDOKU_FRAMEBUFFER', 'AIDOKU_TEST_ROOT', 'AIDOKU_FAKE_WM'}}
    fixture.environment = dict(module_env, PATH=str(fixture.bin), LC_ALL='C',
                               AIDOKU_FAKE_CAPTURE_SLEEP_CAPABILITY='0', AIDOKU_FAKE_EXIT_CODE='7')
    for name in HOST_TOOLS:
        system = Path('/usr/bin') / name
        require(system.is_file(), f'Host fixture tool is unavailable: {system}')
        (fixture.bin / name).symlink_to(system)
    fixture.shell(fixture.bin / 'ps', 'printf "%s\n" ps >>"$AIDOKU_TEST_ROOT/forbidden-tools"; exit 97')
    fixture.shell(fixture.bin / 'pidof', '''
if [ "$#" -eq 1 ] && [ "$1" = awesome ]; then
    printf '%s\n' "$AIDOKU_FAKE_WM"
else
    printf '%s\n' "pidof:$*" >>"$AIDOKU_TEST_ROOT/forbidden-tools"
    exit 97
fi
''')
    original = fixture.fixture_launcher.read_text()
    fixture.fixture_launcher.write_text(original.replace('/mnt/us/aidoku', str(fixture.home))
                                       .replace('/tmp/aidoku-launcher.lock', str(fixture.lock))
                                       .replace('/dev/fb0', '/dev/null'))
    fixture.python(fixture.home / 'app/aidoku-startup-portrait', NATIVE_PORTRAIT)
    (fixture.home / 'data/startup-options').write_text(f'portrait={mode}\nprewarm=0\nresident=off\n')


def run_case(module, entry, mode, retire=False):
    fixture = module.LauncherTests('test_exit_code_and_stock_repaint_are_preserved')
    row = {'entry': entry, 'configured_portrait': mode, 'retire_library_waiter': retire}
    try:
        setup(fixture, mode)
        if not retire:
            fixture.environment['AIDOKU_FAKE_EXIT_AFTER'] = '0.8'
        fixture.log = (fixture.root / 'launcher.log').open('wb')
        options = {'foreground': ['--foreground'], 'library': ['--library'], 'kual': []}
        fixture.launcher = subprocess.Popen(['sh', str(fixture.fixture_launcher), *options[entry]],
                                           env=fixture.environment, stdout=fixture.log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
        parent = fixture.launcher.pid
        expected_parent_status = {'foreground': 7, 'library': 7, 'kual': 0}[entry]
        if entry == 'kual':
            row['entry_exit_status'] = fixture.launcher.wait(timeout=3)
            require(row['entry_exit_status'] == 0, 'Detached entry must return zero before the worker exits')
        module.wait_for(lambda: fixture.phase() == 'active' and bool(fixture.traces('started')),
                        seconds=7, description='fixture app running')
        worker = int((fixture.lock / 'pid').read_text())
        app = fixture.traces('started')[0]['pid']
        row.update(entry_pid=parent, worker_pid=worker, worker_session=os.getsid(worker), app_pid=app,
                   entry_alive_while_active=fixture.launcher.poll() is None)
        require(worker in tree(os.getpid()) and app in tree(worker), 'Worker and app must belong to this fixture')
        require(row['worker_session'] == worker, 'Worker must lead its own session')
        require((parent == worker) == (entry == 'foreground'), 'Unexpected worker separation')
        require(row['entry_alive_while_active'] == (entry != 'kual'), 'Unexpected entry lifetime while app is active')
        require(fixture.statusbar.read_text() == 'stopped' and fixture.process_state(fixture.wm.pid).startswith('T'),
                'Mock stock display was not quiesced around the App')
        expected_worker_status = 7
        if retire:
            fixture.launcher.kill()
            expected_parent_status = -signal.SIGKILL
            require(fixture.launcher.wait(timeout=3) == expected_parent_status, 'Library waiter did not retire')
            fixture.park(app)
            fixture.wake()
            require(fixture.traces('resumed')[-1]['pid'] == app, 'Detached worker did not resume the same app')
            row['same_app_survived_waiter_retirement_and_power_cycle'] = True
            require(app in tree(worker), 'App is no longer owned by the independent worker')
            os.kill(app, signal.SIGTERM)
            expected_worker_status = 0
        row['entry_exit_status'] = fixture.launcher.wait(timeout=10)
        require(row['entry_exit_status'] == expected_parent_status, 'Unexpected entry exit status')
        module.wait_for(lambda: not fixture.lock.exists(), seconds=10, description='worker removed its lock')
        fixture.log.close()
        worker_log = (fixture.root / 'launcher.log' if entry == 'foreground' else
                      fixture.home / 'data/logs/launcher.log')
        row['worker_log'] = worker_log.read_text()
        engine = re.findall(r'^aidoku: Startup portrait supervisor: (native|shell)\.$', row['worker_log'], re.M)
        require(engine == [mode], f'Unexpected startup portrait engine log: {engine}')
        statuses = re.findall(r'^aidoku: Launcher worker exited with status (\d+)\.$', row['worker_log'], re.M)
        require(statuses == [str(expected_worker_status)], f'Unexpected worker exit status: {statuses}')
        row['worker_exit_status'] = int(statuses[0])
        row['app_trace'] = fixture.traces()
        require(len(fixture.traces('started')) == 1, 'The fixture App started more than once')
        require(len(fixture.traces('terminated' if retire else 'ordinary-exit')) == 1,
                'Expected a single ordinary App exit')
        row['portrait_startup'] = fixture.portrait_queries(action='startup')
        require([value['event'] for value in row['portrait_startup']] == ['started', 'published'],
                'Expected one published startup portrait reading')
        reader_state = row['portrait_startup'][0]
        require(reader_state['observer_command_published'] and reader_state['statusbar'] == 'running'
                and not reader_state['wm_state'].startswith(('T', 't')), 'Portrait ran after taking stock display')
        helper = fixture.root / 'native-helper.jsonl'
        row['native_helper'] = [json.loads(line) for line in helper.read_text().splitlines()] if helper.exists() else []
        require(len(row['native_helper']) == int(mode == 'native'), 'Incorrect native helper branch')
        fixture.assert_recovered()
        row['operations'] = fixture.operations.read_text().splitlines()
        require(row['operations'].count('stop:statusbar') == 1 + retire and
                row['operations'].count('start:statusbar') == 1 + retire and
                row['operations'].count('xrefresh:-display :0 -none') == 1,
                'Stock statusbar and redraw operations did not match the lifecycle')
        require(not (fixture.root / 'forbidden-tools').exists(), 'A launcher attempted a forbidden process scan')
        def launcher_children_gone():
            reap_owned({fixture.wm.pid})
            return tree(os.getpid()) == {fixture.wm.pid}
        module.wait_for(launcher_children_gone, seconds=4, description='all owned launcher descendants reaped')
        row.update(lock_removed=True, owned_descendants_reaped=True, stock_restored=True, passed=True)
    except Exception:
        row.update(passed=False, error=traceback.format_exc())
        if hasattr(fixture, 'root'):
            logs = [fixture.root / 'launcher.log', fixture.home / 'data/logs/launcher.log', fixture.operations]
            row['failure_logs'] = {str(path.relative_to(fixture.root)): path.read_text() for path in logs if path.is_file()}
    finally:
        stop_owned()
        if getattr(fixture, 'log', None) is not None:
            fixture.log.close()
        if hasattr(fixture, 'event_descriptor'):
            os.close(fixture.event_descriptor)
        if hasattr(fixture, 'temporary'):
            fixture.temporary.cleanup()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('launcher', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    require(os.uname().sysname == 'Linux', 'Run through ssh test-env')
    require(ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0, 'Could not become a fixture subreaper')
    require(sha(FROZEN_TEST.read_bytes()) == FROZEN_TEST_SHA, 'Unexpected frozen fixture identity')
    script = args.launcher.read_bytes()
    device_paths = set(re.findall(r'(?<![A-Za-z0-9_$}])/(?:bin|dev|etc|mnt|proc|run|sys|tmp|usr|var)/[\w/.-]+', script.decode('ascii')))
    require(device_paths <= ALLOWED_ABSOLUTE, f'Unexpected absolute host/device paths: {device_paths - ALLOWED_ABSOLUTE}')
    require(not tree(os.getpid()), 'The fixture driver started with unrelated children')
    spec = importlib.util.spec_from_file_location('frozen_fixture', FROZEN_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LAUNCHER = args.launcher
    rows = [run_case(module, entry, mode, retire) for mode in ('shell', 'native') for entry, retire in
            (('foreground', False), ('library', False), ('kual', False), ('library', True))]
    result = {
        'passed': all(row['passed'] for row in rows),
        'environment': 'test-env Linux, private PATH and fixture-owned subtree; no Kindle access',
        'launcher': str(args.launcher),
        'suite': str(Path(__file__).resolve()),
        'sha256': {'launcher': sha(script), 'suite': sha(Path(__file__).read_bytes()), 'frozen_fixture': FROZEN_TEST_SHA,
                   'fake_app_source': sha(module.FAKE_APP.encode()), 'native_portrait_mock_source': sha(NATIVE_PORTRAIT.encode())},
        'cases': rows,
    }
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(f'{args.output}: {sum(row["passed"] for row in rows)}/{len(rows)} passed')
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
