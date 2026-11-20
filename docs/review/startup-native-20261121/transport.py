"""One host lease and bounded, pinned SSH connections for a physical batch."""

import argparse
from contextlib import contextmanager
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys


SIGNALS = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def transition():
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def saved(path, value):
    with path.open('x') as output:
        json.dump(value, output, indent=2)
        output.write('\n')


class Device:
    def __init__(self, work, name, owner, label=None, existing=None):
        if (sys.platform != 'linux' or not Path('/opt/kindle-toolchain/ubuntu-run').is_file()
                or work.resolve(strict=True) != work or work.stat().st_mode & 0o077):
            raise ValueError('Use the private project directory on the test-env host')
        os.umask(0o077)
        self.label = label or secrets.token_hex(8)
        if not re.fullmatch('[0-9a-f]{16}', self.label) or not re.fullmatch('[a-z0-9-]+', name):
            raise ValueError('Invalid batch identity')
        self.lease = load('native_entry_lease', work/'tools/device_lease.py')
        self.lease.PROJECT = 'Aidoku'
        self.ssh = load('native_entry_ssh', work/'tools/run_hardware_smoke.py')
        config = Path('/root/.config/kindle-physical')
        self.argv = self.ssh.ssh_argv(config/'ssh_config', 'kindle-physical', config/'known_hosts')
        self.folder = work/'batches'/f'{name}-{self.label}'
        if existing is None:
            self.folder.mkdir(mode=0o700)
        elif self.folder != existing or self.folder.resolve(strict=True) != existing:
            raise ValueError('Incorrect recovery directory')
        self.request = self.lease.LeaseRequest(self.ssh.LEASE, self.folder/'token', owner, 'aidoku-'+self.folder.name)
        self.record = {'mode': name, 'operations': []}
        self.held = False

    def acquire(self):
        with transition():
            self.record['acquisition'] = self.lease.acquire(self.request)
            self.held = True

    def release(self):
        with transition():
            result = self.lease.release(self.request, native_restored_and_connections_closed=True)
            self.record['release'] = result
            self.held = False
            if not (result['owner_and_token_checked'] and result['lease_directory_absent_after_release']):
                raise RuntimeError('release_not_verified')

    def call(self, operation, command, data=b'', timeout=30):
        if not self.held or not self.lease.status(self.request)['ownership_verified']:
            raise RuntimeError('foreign_lease')
        status, stdout, stderr = self.ssh.exchange([*self.argv, command], data, timeout)
        tags = re.findall(rb'^(?:refused |visit refused: )([a-z0-9_]{1,60})$', stderr, re.M)
        reason = tags[-1].decode() if tags else None
        self.record['operations'].append({'operation': operation, 'status': status, 'reason': reason,
                                         'refusals': [tag.decode() for tag in tags[:10]]})
        if status != 0:
            raise RuntimeError(reason or f'{operation}_exit_{status}')
        return stdout


def interrupt(*_):
    raise KeyboardInterrupt


def stock_capture(work, owner):
    script = (Path(__file__).parent/'stock_view.sh').read_bytes()
    device = Device(work, 'stock-view', owner)
    try:
        try:
            device.acquire()
            result = device.call('read_idle_view', 'exec sh -s', script, 25)
        finally:
            if device.held:
                device.release()
        header, compressed = result.split(b'\n', 1)
        if not re.fullmatch(rb'ASTOCK1 [13] (0|2480) (1872,4960|18(60|72),2480)', header):
            raise ValueError('unsupported_stock_geometry')
        if len(compressed) > 5_000_000 or len(gzip.decompress(compressed)) != 1872 * 2480:
            raise ValueError('invalid_stock_raster')
        (device.folder/'private-stock.gray.gz').write_bytes(compressed)
        device.record['geometry'] = header.decode().split()[1:]
        device.record['ok'] = True
    except Exception as error:
        device.record['error'] = getattr(error, 'code', str(error))
    finally:
        saved(device.folder/'receipt.json', device.record)
    print(device.folder, device.record.get('error', 'ok'), flush=True)
    return 0 if device.record.get('ok') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('owner')
    args = parser.parse_args()
    for signum in SIGNALS:
        signal.signal(signum, interrupt)
    raise SystemExit(stock_capture(args.work, args.owner))
