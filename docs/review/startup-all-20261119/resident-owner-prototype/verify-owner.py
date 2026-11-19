#!/usr/bin/env python3
"""Exercise the stock Rust portrait verifier with real, private Linux process trees.

The fixture never has a successful hardware observation and never opens device
nodes. Fresh-calibration records in the last case are *inputs*, not proof that
stock firmware or the production writer published them.
"""

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ORIGINALS = (
    'src/native_presentation.rs',
    'src/native_presentation/portrait_writer.rs',
    'src/native_query.rs',
)


def require(condition, why):
    if not condition:
        raise RuntimeError(why)


def identity(pid):
    base = Path('/proc', str(pid))
    status = (base / 'stat').read_text().rsplit(') ', 1)[1].split()
    require(status[0] not in ('Z', 'X', 'x'), 'The fixture process is not live')
    executable = base / 'exe'
    stat = executable.stat()
    return dict(pid=pid, parent=int(status[1]), group=int(status[2]), session=int(status[3]),
                birth=int(status[19]), executable=dict(path=os.readlink(executable),
                                                       device=stat.st_dev, inode=stat.st_ino))


def write_marker(directory, leaf, content):
    pending = directory / (leaf + '.fixture')
    with open(pending, 'x', encoding='utf-8') as output:
        output.write(content + '\n')
    os.replace(pending, directory / leaf)


def send(stream, command):
    stream.write((command + '\n').encode())
    stream.flush()


def receive(stream):
    readable, _, _ = select.select([stream], [], [], 5)
    require(readable, 'Timed out waiting for a private fixture process')
    line = stream.readline(4096)
    require(line and line.endswith(b'\n'), 'Invalid fixture response')
    return json.loads(line)


def child_role(role, values):
    directory = Path(values[0])
    executable = values[1]
    me = str(Path(__file__).resolve())
    if role == 'waiter':
        owner_pipe, release = map(int, values[2:])
        owner = subprocess.Popen([sys.executable, me, '--child', 'owner', str(directory), executable,
                                  str(owner_pipe)], pass_fds=(owner_pipe,), start_new_session=True)
        os.close(owner_pipe)
        # The native-handler waiter intentionally leaves while the original worker is alive.
        try:
            require(os.read(release, 1) == b'R', 'Missing waiter return request')
        finally:
            os.close(release)
        return 0
    if role == 'runner':
        return subprocess.run([executable, 'participant']).returncode
    if role == 'reader':
        gate = int(values[2])
        try:
            require(os.read(gate, 1) == b'G', 'Unadmitted portrait reader')
        finally:
            os.close(gate)
        return subprocess.run([executable, 'capture']).returncode
    if role == 'owner':
        with socket.socket(fileno=int(values[2])) as channel, channel.makefile('rwb', 0) as control:
            runner = subprocess.Popen([sys.executable, me, '--child', 'runner', str(directory), executable])
            try:
                for message in control:
                    if message == b'quit\n':
                        break
                    require(message == b'capture\n', 'Unknown fixture owner request')
                    read_gate, release = os.pipe()
                    with subprocess.Popen([sys.executable, me, '--child', 'reader', str(directory),
                                           executable, str(read_gate)], start_new_session=True,
                                          pass_fds=(read_gate,), stdout=subprocess.PIPE) as reader:
                        os.close(read_gate)
                        try:
                            nonce = os.environ['AIDOKU_LAUNCH_NONCE']
                            write_marker(directory, 'native-read-go', f'{reader.pid}:1:{nonce}')
                            os.write(release, b'G')
                        finally:
                            os.close(release)
                        result, _ = reader.communicate(timeout=4)
                        require(reader.returncode == 0, 'The fixture writer failed to report')
                        control.write(result)
            finally:
                runner.wait(timeout=5)
        return runner.returncode
    raise ValueError(role)


def build(source, destination, lock):
    files = {}
    for rel in ORIGINALS:
        content = (source / rel).read_bytes()
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files[rel] = hashlib.sha256(content).hexdigest()
    fixture = Path(__file__).resolve().parent
    shutil.copyfile(fixture / 'probe.rs', destination / 'src/main.rs')
    shutil.copyfile(fixture / 'Cargo.toml', destination / 'Cargo.toml')
    shutil.copyfile(source / 'Cargo.lock', destination / 'Cargo.lock')
    env = dict(os.environ, CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='6')
    cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
    with open(lock, 'a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        subprocess.run([cargo, 'build', '--offline', '-j6', '--manifest-path',
                        str(destination / 'Cargo.toml')], cwd=destination, check=True, env=env)
    return destination / 'target/debug/aidoku-owner-boundary-probe', files


def verify(binary):
    os.umask(0o077)
    libc = ctypes.CDLL(None, use_errno=True)
    # Adopt and reap our own fixture worker when its original Library waiter exits.
    require(libc.prctl(36, 1, 0, 0, 0) == 0, 'Cannot own the orphaned test worker')
    with tempfile.TemporaryDirectory(prefix='owner-verifier-') as name:
        directory = Path(name)
        nonce = secrets.token_hex(16)
        env = dict(os.environ, AIDOKU_LAUNCHER_DIR=str(directory), AIDOKU_LAUNCH_NONCE=nonce,
                   AIDOKU_LAUNCH_GENERATION='1')
        local, peer = socket.socketpair()
        release, leave = os.pipe()
        waiter = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', 'waiter',
                                   name, str(binary), str(peer.fileno()), str(release)], env=env,
                                  pass_fds=(peer.fileno(), release), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, bufsize=0)
        peer.close()
        os.close(release)
        owner_pid = None
        raw = {}
        def command(verb):
            send(waiter.stdin, verb)
            return receive(waiter.stdout)
        def rejected(name, value, reason):
            require(value == {'ok': False, 'error': reason, 'hardware_calls': 0}, name + ': ' + str(value))
            raw[name] = value
        def setup(generation, owner, direction='FIXED'):
            record = dict(version=1, generation=generation, nonce=nonce, launcher=owner,
                          publisher=participant['executable'], profile='pw5', native_mode='Y8',
                          native_direction=direction, raw_rotation=0, layout='1:2:3')
            write_marker(directory, 'pid', str(owner['pid']))
            write_marker(directory, 'nonce', nonce)
            write_marker(directory, 'generation', f'{generation}:{nonce}')
            write_marker(directory, 'native-portrait-calibration', json.dumps(record, separators=(',', ':')))
            return record
        try:
            with local, local.makefile('rwb', 0) as control:
                participant = identity(receive(waiter.stdout)['pid'])
                runner = identity(participant['parent'])
                owner = identity(runner['parent'])
                owner_pid = owner['pid']
                require(owner['parent'] == waiter.pid and owner['group'] == owner['session'] == owner_pid,
                        'The fixture does not match the foreground worker ancestry')
                setup(1, owner)
                write_marker(directory, 'application-pid', str(participant['pid']))
                loaded = command('load')
                require(loaded == {'ok': True, 'error': None, 'hardware_calls': 0}, str(loaded))
                raw['original_context_opened'] = loaded
                control.write(b'capture\n')
                rejected('new_capture_with_live_application', receive(control),
                         'Startup portrait publication follows application initialization')
                (directory / 'application-pid').unlink()
                write_marker(directory, 'sleep-request', f"{participant['pid']} {nonce} 1")
                control.write(b'capture\n')
                rejected('new_capture_with_outstanding_sleep', receive(control),
                         'Startup portrait publication follows application initialization')
                write_marker(directory, 'application-pid', str(participant['pid']))
                write_marker(directory, 'epoch', '1')
                write_marker(directory, 'resume-request', f"{participant['pid']} {nonce} 1")
                setup(1, owner, 'D')
                rejected('replaced_anchor_in_old_context', command('startup'),
                         'The native portrait calibration changed during this run')
                rejected('sleep_signal_cannot_accept_replaced_anchor', command('resume'),
                         'The native portrait calibration changed during this run')
                setup(2, owner)
                rejected('next_generation_in_old_context', command('startup'),
                         'The native presentation belongs to another launcher generation')
                setup(1, owner)
                os.write(leave, b'R')
                os.close(leave)
                leave = -1
                require(waiter.wait(timeout=3) == 0, 'First native waiter did not exit cleanly')
                deadline = time.monotonic() + 2
                while identity(owner_pid)['parent'] == waiter.pid and time.monotonic() < deadline:
                    time.sleep(.005)
                after = identity(owner_pid)
                require(after == {**owner, 'parent': os.getpid()} and identity(runner['pid']) == runner
                        and identity(participant['pid']) == participant,
                        'The retained worker unexpectedly changed more than its parent')
                raw['after_first_waiter_return'] = {'original_waiter_exited': True,
                    'worker_same_pid_birth_executable': True, 'worker_parent_changed': True,
                    'runner_full_identity_unchanged': True, 'participant_full_identity_unchanged': True}
                rejected('same_pid_resume_after_waiter_exit', command('resume'),
                         'The native launcher identity changed')
                rejected('old_anchor_after_waiter_exit', command('startup'),
                         'The native launcher identity changed')
                setup(2, identity(os.getpid()))
                rejected('new_owner_cannot_claim_old_participant', command('load'),
                         'The application is not owned by the native presentation launcher')
                setup(2, after)
                next_context = command('load')
                require(next_context == loaded, 'A newly constructed context did not bind the same retained process')
                reached = command('startup')
                require(reached['ok'] is False and reached['hardware_calls'] == 1 and
                        reached['error'] == 'Observe the native framebuffer without acquiring display ownership',
                        'Expected a fresh observation, without a fixture supplying any hardware result')
                raw['fresh_context_same_participant_synthetic_anchor'] = {
                    'context': next_context, 'fresh_probe_required': reached,
                    'record_source': 'test fixture only; not a native writer',
                    'hardware_observation_succeeded': False}
                send(waiter.stdin, 'quit')
                control.write(b'quit\n')
        finally:
            if leave >= 0:
                os.close(leave)
            waiter.stdin.close()
            waiter.stdout.close()
            if waiter.poll() is None:
                waiter.wait(timeout=5)
            if owner_pid is not None:
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    try:
                        finished, status = os.waitpid(owner_pid, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if finished == owner_pid:
                        require(os.waitstatus_to_exitcode(status) == 0, 'The private worker did not retire')
                        break
                    time.sleep(.01)
                else:
                    raise RuntimeError('The private fixture worker was not reaped')
        return raw


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--child':
        return child_role(sys.argv[2], sys.argv[3:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--build-dir', required=True, type=Path)
    parser.add_argument('--build-lock', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    destination = args.build_dir.resolve()
    destination.mkdir(exist_ok=True, parents=True)
    binary, sources = build(args.source.resolve(), destination, args.build_lock)
    value = {'schema': 1, 'measurement': 'Linux host; unchanged production Rust verifier and private processes',
             'native_firmware': False, 'slint_gui_exercised': False, 'sources_sha256': sources,
             'observations': verify(binary)}
    encoded = json.dumps(value, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(encoded, encoding='utf-8')
    print(encoded, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
